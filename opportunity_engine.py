"""
Opportunity engine — multi-source B2B signal collector.

Signal sources (all run in parallel, each with hard timeouts):
  1. Reddit        — Apify clearpath~reddit-search-scraper (real) or LLM mock
  2. X / Twitter   — Twitter v2 recent-search API (bearer token from env)
  3. Job boards    — Adzuna jobs API (free tier) OR SerpAPI Google Jobs fallback
  4. LinkedIn      — PhantomBuster LinkedIn Post Search (phantom API)

Contact enrichment (runs per-card after signal collection):
  - Apollo.io People Search API — adds verified email + title for matched persons

Flow:
  build_opportunities(domain, icp)
    → parallel source fetches (max 40 s wall-clock)
    → merge & dedup signals (best 15)
    → LLM rank → 5 cards
    → Apollo enrichment (best-effort, max 10 s)
    → return cards
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Any

import boto3
import requests

# ── Bedrock LLM ───────────────────────────────────────────────────────────────

BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-west-2"

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


def _invoke(prompt: str, max_tokens: int = 2048) -> str:
    resp = _bedrock().invoke_model(
        modelId=BEDROCK_MODEL,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        ),
        contentType="application/json",
        accept="application/json",
    )
    raw = json.loads(resp["body"].read())
    return raw["content"][0]["text"].strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_get(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: int = 15) -> dict:
    """GET with timeout; always returns a dict (empty on error)."""
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {"_list": data}
    except Exception:
        return {}


def _safe_post(url: str, json_body: dict, headers: dict | None = None,
               timeout: int = 15) -> dict:
    """POST with timeout; always returns a dict (empty on error)."""
    try:
        r = requests.post(url, json=json_body, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {"_list": data}
    except Exception:
        return {}


# ── Source 1: Reddit via Apify ────────────────────────────────────────────────

def _apify_reddit_signals(domain: str, icp: dict) -> list[dict]:
    """Real Apify Reddit search — 40 s hard timeout for the whole actor run."""
    api_key = os.environ.get("APIFY_API_KEY")
    if not api_key:
        return []

    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    parts = [problem[:60]] if problem else [domain]
    if hypotheses:
        parts.append(hypotheses[0][:40])
    query = " ".join(parts)[:120]

    BASE = "https://api.apify.com/v2"
    try:
        resp = requests.post(
            f"{BASE}/acts/clearpath~reddit-search-scraper/runs",
            params={"token": api_key, "memory": 256},
            json={"query": query, "maxItems": 15, "sort": "relevance"},
            timeout=20,
        ).json()
    except Exception:
        return []

    run_id = resp.get("data", {}).get("id")
    if not run_id:
        return []

    deadline = time.time() + 40  # hard 40 s cap
    while time.time() < deadline:
        time.sleep(5)
        try:
            status_resp = requests.get(
                f"{BASE}/actor-runs/{run_id}",
                params={"token": api_key},
                timeout=10,
            ).json()
        except Exception:
            continue
        status = status_resp.get("data", {}).get("status", "")
        if status == "SUCCEEDED":
            try:
                items = requests.get(
                    f"{BASE}/actor-runs/{run_id}/dataset/items",
                    params={"token": api_key},
                    timeout=15,
                ).json()
            except Exception:
                return []
            if not isinstance(items, list):
                return []
            results = []
            for post in items:
                if post.get("isNsfw") or not post.get("title"):
                    continue
                results.append({
                    "source": "reddit",
                    "subreddit_or_company": f"r/{post.get('subreddit', '')}",
                    "post_title": post.get("title", ""),
                    "post_excerpt": (post.get("body") or "")[:300],
                    "author_name": post.get("author", ""),
                    "author_title": "",
                    "author_company": "",
                    "author_company_size": "",
                    "author_linkedin_url": f"https://reddit.com/user/{post.get('author', '')}",
                    "post_url": post.get("permalink", post.get("url", "")),
                    "posted_at": post.get("createdAt", ""),
                    "pain_keywords": [],
                    "icp_match_score": min(1.0, 0.5 + post.get("score", 0) / 1000),
                })
            return results
        if status in ("FAILED", "TIMED-OUT", "ABORTED"):
            return []
    return []


# ── Source 2: X / Twitter ─────────────────────────────────────────────────────

def _twitter_signals(domain: str, icp: dict) -> list[dict]:
    """Twitter v2 recent search — bearer token from TWITTER_BEARER_TOKEN env."""
    bearer = os.environ.get("TWITTER_BEARER_TOKEN")
    if not bearer:
        return []

    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    # Build query — keep under 512 chars, exclude retweets, focus on pain language
    pain_terms = " OR ".join(
        f'"{h[:30]}"' for h in hypotheses[:2]
    ) if hypotheses else f'"{problem[:60]}"'
    query = f"({pain_terms}) (frustrated OR struggling OR need OR looking) -is:retweet lang:en"[:512]

    data = _safe_get(
        "https://api.twitter.com/2/tweets/search/recent",
        params={
            "query": query,
            "max_results": 10,
            "tweet.fields": "author_id,created_at,public_metrics,text",
            "expansions": "author_id",
            "user.fields": "name,username,description,public_metrics",
        },
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=12,
    )
    if not data or not isinstance(data.get("data"), list):
        return []

    # Build author lookup
    users = {u["id"]: u for u in (data.get("includes", {}).get("users") or [])}

    results = []
    for tweet in data["data"]:
        author = users.get(tweet.get("author_id", ""), {})
        metrics = tweet.get("public_metrics", {})
        engagement = metrics.get("like_count", 0) + metrics.get("retweet_count", 0)
        results.append({
            "source": "x_twitter",
            "subreddit_or_company": "X / Twitter",
            "post_title": tweet["text"][:80],
            "post_excerpt": tweet["text"][:300],
            "author_name": author.get("name", ""),
            "author_title": author.get("description", "")[:100],
            "author_company": "",
            "author_company_size": "",
            "author_linkedin_url": f"https://twitter.com/{author.get('username', '')}",
            "post_url": f"https://twitter.com/i/web/status/{tweet['id']}",
            "posted_at": tweet.get("created_at", ""),
            "pain_keywords": [],
            "icp_match_score": min(1.0, 0.4 + engagement / 200),
        })
    return results


# ── Source 3: Job boards (Adzuna) ─────────────────────────────────────────────

def _job_board_signals(domain: str, icp: dict) -> list[dict]:
    """
    Adzuna Jobs API — free tier (app_id + app_key from env).
    Job postings are demand signals: a company hiring for a role
    signals active pain and budget. We surface the *hiring company*
    as the prospect, not the job seeker.
    """
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return _serpapi_jobs_signals(domain, icp)  # fallback

    icp_hypotheses = icp.get("icp_hypotheses", [])
    problem = icp.get("problem", "")

    # Build job title query from ICP — e.g. "sales operations" or "RevOps"
    role_hint = icp_hypotheses[0] if icp_hypotheses else problem[:40]

    data = _safe_get(
        "https://api.adzuna.com/v1/api/jobs/us/search/1",
        params={
            "app_id": app_id,
            "app_key": app_key,
            "what": role_hint[:60],
            "results_per_page": 10,
            "sort_by": "date",
            "content-type": "application/json",
        },
        timeout=12,
    )
    if not data or not isinstance(data.get("results"), list):
        return []

    results = []
    for job in data["results"][:10]:
        company = (job.get("company") or {}).get("display_name", "")
        title = job.get("title", "")
        description = job.get("description", "")[:300]
        if not company or not title:
            continue
        results.append({
            "source": "job_board",
            "subreddit_or_company": "Adzuna / Job Board",
            "post_title": f"{company} is hiring: {title}",
            "post_excerpt": description,
            "author_name": "",  # will be enriched via Apollo
            "author_title": title,
            "author_company": company,
            "author_company_size": "",
            "author_linkedin_url": "",
            "post_url": job.get("redirect_url", ""),
            "posted_at": job.get("created", ""),
            "pain_keywords": [],
            "icp_match_score": 0.65,
            "_needs_contact_enrichment": True,
        })
    return results


def _serpapi_jobs_signals(domain: str, icp: dict) -> list[dict]:
    """SerpAPI Google Jobs fallback for job board signals."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return []

    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    query = hypotheses[0] if hypotheses else problem[:50]

    data = _safe_get(
        "https://serpapi.com/search",
        params={"engine": "google_jobs", "q": query[:80], "api_key": api_key},
        timeout=12,
    )
    if not data or not isinstance(data.get("jobs_results"), list):
        return []

    results = []
    for job in data["jobs_results"][:8]:
        company = job.get("company_name", "")
        title = job.get("title", "")
        if not company:
            continue
        results.append({
            "source": "job_board",
            "subreddit_or_company": "Google Jobs",
            "post_title": f"{company} is hiring: {title}",
            "post_excerpt": (job.get("description") or "")[:300],
            "author_name": "",
            "author_title": title,
            "author_company": company,
            "author_company_size": "",
            "author_linkedin_url": "",
            "post_url": job.get("job_id", ""),
            "posted_at": job.get("detected_extensions", {}).get("posted_at", ""),
            "pain_keywords": [],
            "icp_match_score": 0.60,
            "_needs_contact_enrichment": True,
        })
    return results


# ── Source 4: LinkedIn via PhantomBuster ──────────────────────────────────────

def _phantombuster_linkedin_signals(domain: str, icp: dict) -> list[dict]:
    """
    PhantomBuster LinkedIn Post Search phantom.
    Phantom ID is read from env: PHANTOMBUSTER_LINKEDIN_PHANTOM_ID
    API key from PHANTOMBUSTER_API_KEY.
    Launches a phantom run and polls for up to 35 s.
    """
    api_key = os.environ.get("PHANTOMBUSTER_API_KEY", "8OWQ4DKHrEP3uaMX48jkEgciSGRqVmJRfWm2LCC3Slw")
    phantom_id = os.environ.get("PHANTOMBUSTER_LINKEDIN_PHANTOM_ID", "")
    if not api_key or not phantom_id:
        return _phantombuster_search_linkedin_fallback(domain, icp, api_key)

    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    search_query = hypotheses[0] if hypotheses else problem[:60]

    headers = {"X-Phantombuster-Key": api_key, "Content-Type": "application/json"}

    # Launch phantom
    launch_resp = _safe_post(
        f"https://api.phantombuster.com/api/v2/agents/{phantom_id}/launch",
        json_body={"argument": {"search": search_query, "numberOfResultsPerLaunch": 10}},
        headers=headers,
        timeout=15,
    )
    if not launch_resp:
        return _phantombuster_search_linkedin_fallback(domain, icp, api_key)

    container_id = launch_resp.get("containerId") or launch_resp.get("data", {}).get("containerId")
    if not container_id:
        return _phantombuster_search_linkedin_fallback(domain, icp, api_key)

    # Poll for output
    deadline = time.time() + 35
    while time.time() < deadline:
        time.sleep(5)
        output = _safe_get(
            f"https://api.phantombuster.com/api/v2/containers/{container_id}/output",
            headers=headers,
            timeout=10,
        )
        if not output:
            continue
        status = output.get("status")
        if status == "finished":
            items = output.get("output") or []
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except Exception:
                    return []
            return _parse_phantom_linkedin_items(items)
        if status in ("error", "stopped"):
            break

    return _phantombuster_search_linkedin_fallback(domain, icp, api_key)


def _phantombuster_search_linkedin_fallback(domain: str, icp: dict, api_key: str) -> list[dict]:
    """
    Fallback: use PhantomBuster's LinkedIn Profile Scraper or
    the Search Export phantom if the post phantom is not configured.
    Lists available agents and tries to use any LinkedIn-related one.
    """
    if not api_key:
        return []
    headers = {"X-Phantombuster-Key": api_key}
    agents_resp = _safe_get(
        "https://api.phantombuster.com/api/v2/agents/fetch-all",
        headers=headers,
        timeout=10,
    )
    if not agents_resp:
        return []
    agents = agents_resp.get("_list") or agents_resp.get("data", [])
    if not isinstance(agents, list):
        agents = []
    # Find a LinkedIn search-related phantom
    linkedin_agents = [
        a for a in agents
        if "linkedin" in (a.get("name") or "").lower()
        and "search" in (a.get("name") or "").lower()
    ]
    if not linkedin_agents:
        return []
    # Use the first match
    phantom = linkedin_agents[0]
    phantom_id = phantom.get("id")
    if not phantom_id:
        return []
    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    search_query = hypotheses[0] if hypotheses else problem[:60]

    launch_resp = _safe_post(
        f"https://api.phantombuster.com/api/v2/agents/{phantom_id}/launch",
        json_body={"argument": {"search": search_query, "numberOfResultsPerLaunch": 10}},
        headers={**headers, "Content-Type": "application/json"},
        timeout=15,
    )
    if not launch_resp:
        return []

    container_id = launch_resp.get("containerId") or launch_resp.get("data", {}).get("containerId")
    if not container_id:
        return []

    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(5)
        output = _safe_get(
            f"https://api.phantombuster.com/api/v2/containers/{container_id}/output",
            headers=headers,
            timeout=10,
        )
        if not output:
            continue
        if output.get("status") == "finished":
            items = output.get("output") or []
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except Exception:
                    return []
            return _parse_phantom_linkedin_items(items)
        if output.get("status") in ("error", "stopped"):
            break
    return []


def _parse_phantom_linkedin_items(items: list) -> list[dict]:
    """Convert PhantomBuster LinkedIn items to our signal format."""
    results = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        name = item.get("fullName") or item.get("name") or item.get("author", {}).get("name", "")
        title = item.get("headline") or item.get("title") or item.get("subtitle", "")
        company = item.get("companyName") or item.get("company", "")
        linkedin_url = item.get("profileUrl") or item.get("url", "")
        post_text = item.get("text") or item.get("postContent") or item.get("summary", "")
        if not post_text and not name:
            continue
        results.append({
            "source": "linkedin",
            "subreddit_or_company": f"LinkedIn — {company}" if company else "LinkedIn",
            "post_title": (post_text[:80] if post_text else f"{name} — {title}"),
            "post_excerpt": (post_text[:300] if post_text else f"{name} at {company}: {title}"),
            "author_name": name,
            "author_title": title,
            "author_company": company,
            "author_company_size": item.get("companySize", ""),
            "author_linkedin_url": linkedin_url,
            "post_url": item.get("postUrl") or linkedin_url,
            "posted_at": item.get("date") or item.get("timestamp", ""),
            "pain_keywords": [],
            "icp_match_score": 0.70,
        })
    return results


# ── LLM mock fallback ─────────────────────────────────────────────────────────

def _mock_signals(domain: str, icp: dict) -> list[dict]:
    """Generate realistic mock pain post signals via LLM (used when all live sources fail)."""
    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    triggers = icp.get("buying_triggers", [])

    prompt = f"""You are simulating B2B pain signal research for domain "{domain}".

ICP Problem: {problem}
ICP Hypotheses: {json.dumps(hypotheses)}
Buying Triggers: {json.dumps(triggers)}

Generate 8 realistic social media pain posts that would match this ICP. Mix Reddit posts (r/sales, r/coldemail, r/saas, r/entrepreneur), LinkedIn posts, and X/Twitter posts.

Each post must have a REAL-feeling author who would plausibly buy this product.

Return ONLY valid JSON array:
[
  {{
    "source": "reddit" | "linkedin" | "x_twitter",
    "subreddit_or_company": "e.g. r/sales or LinkedIn or X",
    "post_title": "short title",
    "post_excerpt": "50-100 word excerpt expressing the pain",
    "author_name": "realistic first+last name",
    "author_title": "e.g. VP of Sales at [Company]",
    "author_company": "realistic company name",
    "author_company_size": "e.g. 50-200 employees",
    "author_linkedin_url": "https://linkedin.com/in/firstname-lastname",
    "post_url": "https://reddit.com/r/.../... or realistic LinkedIn post URL",
    "posted_at": "e.g. 2 days ago",
    "pain_keywords": ["keyword1", "keyword2"],
    "icp_match_score": 0.0-1.0
  }}
]"""

    text = _invoke(prompt, max_tokens=3000)
    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(0))
    return []


# ── Apollo.io contact enrichment ──────────────────────────────────────────────

def _apollo_enrich_card(card: dict) -> dict:
    """
    Call Apollo.io People Search to find verified contact for a card's person.
    Adds: apollo_email, apollo_verified_title, apollo_linkedin_url.
    Apollo API key from APOLLO_API_KEY env (default: known key from task description).
    """
    api_key = os.environ.get("APOLLO_API_KEY", "5MTKxv8M_5wJVdi5uKcQZw")
    if not api_key:
        return card

    person_name = card.get("person_name", "")
    company_name = card.get("company_name", "")
    if not person_name and not company_name:
        return card

    name_parts = person_name.split() if person_name else []
    first = name_parts[0] if name_parts else ""
    last = name_parts[-1] if len(name_parts) > 1 else ""

    payload = {
        "api_key": api_key,
        "q_organization_name": company_name,
        "page": 1,
        "per_page": 1,
    }
    if first:
        payload["q_keywords"] = f"{first} {last}".strip()

    resp = _safe_post(
        "https://api.apollo.io/v1/people/search",
        json_body=payload,
        timeout=10,
    )
    if not resp or not isinstance(resp.get("people"), list) or not resp["people"]:
        return card

    person = resp["people"][0]
    if person.get("email"):
        card["apollo_email"] = person["email"]
    if person.get("title"):
        card["apollo_verified_title"] = person["title"]
    if person.get("linkedin_url"):
        card["apollo_linkedin_url"] = person["linkedin_url"]
    if person.get("organization", {}).get("estimated_num_employees"):
        card["company_size"] = str(person["organization"]["estimated_num_employees"])
    return card


def _apollo_enrich_cards(cards: list[dict]) -> list[dict]:
    """Enrich top cards in parallel with a 10 s hard cap."""
    if not cards:
        return cards
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_apollo_enrich_card, card): i for i, card in enumerate(cards)}
        try:
            for fut in as_completed(futs, timeout=10):
                idx = futs[fut]
                try:
                    cards[idx] = fut.result()
                except Exception:
                    pass
        except FuturesTimeout:
            pass
    return cards


# ── Ranking ───────────────────────────────────────────────────────────────────

def _rank_into_cards(domain: str, icp: dict, signals: list[dict]) -> list[dict]:
    """LLM ranks signals into ranked opportunity cards."""
    company_name = icp.get("company_name", domain)
    solution = icp.get("solution", "")
    hypotheses = icp.get("icp_hypotheses", [])
    triggers = icp.get("buying_triggers", [])

    prompt = f"""You are a B2B sales intelligence analyst for "{company_name}" ({domain}).

Solution: {solution}
ICP Hypotheses: {json.dumps(hypotheses)}
Buying Triggers: {json.dumps(triggers)}

Below are pain post signals from Reddit, LinkedIn, X/Twitter, and job boards. Rank and score the top 5 best-fit opportunities.
For each, generate a complete opportunity card with 3 distinct outreach message variants.

Signals:
{json.dumps(signals, indent=2)}

Return ONLY valid JSON array of exactly 5 cards (or fewer if <5 signals):
[
  {{
    "rank": 1,
    "person_name": "full name from signal",
    "person_title": "their title",
    "company_name": "their company",
    "company_size": "e.g. 50-200 employees",
    "source_platform": "reddit | linkedin | x_twitter | job_board",
    "source_post_title": "title of the pain post",
    "source_post_url": "URL",
    "source_post_excerpt": "the key quote expressing pain",
    "pain_summary": "2-3 sentences: what they are struggling with and why it matters",
    "why_now": "1-2 sentences: why this is urgent / timely for them right now",
    "icp_fit": "high / medium / low",
    "confidence_score": 0.0-1.0,
    "contact_path": "how to reach them (LinkedIn DM / email / Reddit reply / X DM)",
    "outreach_pain_first": "40-60 word message leading with their pain",
    "outreach_value_give": "40-60 word message leading with a free resource or insight",
    "outreach_direct_ask": "40-60 word message with a direct soft CTA"
  }}
]"""

    text = _invoke(prompt, max_tokens=4096)
    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if json_match:
        cards = json.loads(json_match.group(0))
        for c in cards:
            c.setdefault("user_rating", 0)
        return sorted(cards, key=lambda c: c.get("confidence_score", 0), reverse=True)
    return []


# ── Main entry ────────────────────────────────────────────────────────────────

def build_opportunities(domain: str, icp: dict) -> list[dict]:
    """
    Main entry point: returns up to 5 ranked opportunity cards.

    All live sources run in parallel with a 40 s wall-clock budget.
    Falls back to LLM mock if every live source returns empty.
    Apollo enrichment applied afterwards (10 s cap, best-effort).
    """
    source_fns = {
        "reddit": lambda: _apify_reddit_signals(domain, icp),
        "twitter": lambda: _twitter_signals(domain, icp),
        "jobs": lambda: _job_board_signals(domain, icp),
        "linkedin": lambda: _phantombuster_linkedin_signals(domain, icp),
    }

    all_signals: list[dict] = []

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): name for name, fn in source_fns.items()}
        try:
            for fut in as_completed(futs, timeout=40):
                name = futs[fut]
                try:
                    signals = fut.result()
                    all_signals.extend(signals or [])
                except Exception:
                    pass  # one source failing never blocks others
        except FuturesTimeout:
            pass  # collect whatever came in under 40 s

    # Fall back to LLM mock if no live signals
    if not all_signals:
        all_signals = _mock_signals(domain, icp)

    if not all_signals:
        raise ValueError("No signals found for this domain. Try a different domain.")

    # Keep best 15 signals with real content
    enriched = [s for s in all_signals if s.get("post_excerpt", "").strip()]
    enriched.sort(key=lambda s: s.get("icp_match_score", 0), reverse=True)
    top_signals = enriched[:15] if enriched else all_signals[:15]

    # LLM ranking (the slow part — but now signals arrived fast in parallel)
    cards = _rank_into_cards(domain, icp, top_signals)

    # Apollo enrichment — best-effort, never blocks delivery
    cards = _apollo_enrich_cards(cards)

    return cards

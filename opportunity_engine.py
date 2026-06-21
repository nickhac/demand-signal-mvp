"""
Opportunity engine.

Flow:
1. Generate mock "pain post" signals (Reddit + LinkedIn style).
   When APIFY_API_KEY is set, uses real Apify actors.
   Otherwise falls back to LLM-generated mock signals.

2. LLM ranks/scores matched signals into opportunity cards.
   Each card: person, company, source_post, pain_summary, why_now,
   icp_fit, confidence_score, contact_path, outreach_variants x3.
"""

import json
import os
import re
import boto3
from typing import Any

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


def _mock_signals(domain: str, icp: dict) -> list[dict]:
    """Generate realistic mock pain post signals via LLM (Apify fallback)."""
    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    triggers = icp.get("buying_triggers", [])

    prompt = f"""You are simulating B2B pain signal research for domain "{domain}".

ICP Problem: {problem}
ICP Hypotheses: {json.dumps(hypotheses)}
Buying Triggers: {json.dumps(triggers)}

Generate 8 realistic social media pain posts that would match this ICP. Mix Reddit posts (r/sales, r/coldemail, r/saas, r/entrepreneur) and LinkedIn posts.

Each post must have a REAL-feeling author who would plausibly buy this product.

Return ONLY valid JSON array:
[
  {{
    "source": "reddit" or "linkedin",
    "subreddit_or_company": "e.g. r/sales or LinkedIn",
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


def _apify_signals(domain: str, icp: dict) -> list[dict]:
    """Real Apify Reddit search via clearpath~reddit-search-scraper (async polling)."""
    import requests
    import time

    api_key = os.environ["APIFY_API_KEY"]
    BASE = "https://api.apify.com/v2"

    # Build a focused query from ICP problem + key terms
    problem = icp.get("problem", "")
    hypotheses = icp.get("icp_hypotheses", [])
    triggers = icp.get("buying_triggers", [])

    # Construct a rich search query (max ~120 chars for Reddit search)
    parts = [problem[:60]] if problem else [domain]
    if hypotheses:
        parts.append(hypotheses[0][:40])
    query = " ".join(parts)[:120]

    def _run_actor(actor: str, input_body: dict, max_wait: int = 120) -> list:
        """Start actor, poll until done, return dataset items."""
        resp = requests.post(
            f"{BASE}/acts/{actor}/runs",
            params={"token": api_key, "memory": 256},
            json=input_body,
            timeout=30,
        ).json()
        run_id = resp.get("data", {}).get("id")
        if not run_id:
            return []

        deadline = time.time() + max_wait
        while time.time() < deadline:
            time.sleep(8)
            status_resp = requests.get(
                f"{BASE}/actor-runs/{run_id}",
                params={"token": api_key},
                timeout=15,
            ).json()
            status = status_resp.get("data", {}).get("status", "")
            if status == "SUCCEEDED":
                items = requests.get(
                    f"{BASE}/actor-runs/{run_id}/dataset/items",
                    params={"token": api_key},
                    timeout=20,
                ).json()
                return items if isinstance(items, list) else []
            if status in ("FAILED", "TIMED-OUT", "ABORTED"):
                return []
        return []

    # Use clearpath~reddit-search-scraper — proven to work with keyword queries
    raw = _run_actor(
        "clearpath~reddit-search-scraper",
        {"query": query, "maxItems": 20, "sort": "relevance"},
    )

    results = []
    for post in raw:
        if post.get("isNsfw") or not post.get("title"):
            continue
        results.append(
            {
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
            }
        )
    return results


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

Below are pain post signals from Reddit and LinkedIn. Rank and score the top 5 best-fit opportunities.
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
    "source_platform": "reddit or linkedin",
    "source_post_title": "title of the pain post",
    "source_post_url": "URL",
    "source_post_excerpt": "the key quote expressing pain",
    "pain_summary": "2-3 sentences: what they are struggling with and why it matters",
    "why_now": "1-2 sentences: why this is urgent / timely for them right now",
    "icp_fit": "high / medium / low",
    "confidence_score": 0.0-1.0,
    "contact_path": "how to reach them (LinkedIn DM / email / Reddit reply)",
    "outreach_pain_first": "40-60 word message leading with their pain",
    "outreach_value_give": "40-60 word message leading with a free resource or insight",
    "outreach_direct_ask": "40-60 word message with a direct soft CTA"
  }}
]"""

    text = _invoke(prompt, max_tokens=4096)
    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if json_match:
        cards = json.loads(json_match.group(0))
        # Ensure user_rating field
        for c in cards:
            c.setdefault("user_rating", 0)
        return sorted(cards, key=lambda c: c.get("confidence_score", 0), reverse=True)
    return []


def build_opportunities(domain: str, icp: dict) -> list[dict]:
    """Main entry point: returns up to 5 ranked opportunity cards."""
    # Use real Apify if key present, else mock
    signals: list[dict] = []
    if os.environ.get("APIFY_API_KEY"):
        try:
            signals = _apify_signals(domain, icp)
        except Exception:
            pass  # fall through to mock

    if not signals:
        signals = _mock_signals(domain, icp)

    if not signals:
        raise ValueError("No signals found for this domain. Try a different domain.")

    return _rank_into_cards(domain, icp, signals)

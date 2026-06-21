"""
ICP extractor — scrapes the domain, then uses Bedrock Claude to extract ICP profile.
"""

import json
import re
import urllib.parse

import boto3
import requests

BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-west-2"

_client = None

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DemandSignal/1.0; "
        "+https://demand-signal-mvp.onrender.com)"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_CONTENT_CHARS = 8000  # trim before sending to LLM


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


def _scrape_domain(domain: str) -> str:
    """
    Fetch the homepage (and /about if short) and return cleaned plain text.
    Returns empty string on failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    def _fetch_text(url: str) -> str:
        try:
            r = requests.get(url, headers=SCRAPE_HEADERS, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                return ""
            soup = BeautifulSoup(r.text, "lxml")
            # Remove noise
            for tag in soup(["script", "style", "nav", "footer",
                              "noscript", "aside", "form", "button",
                              "iframe", "img", "svg"]):
                tag.decompose()
            return " ".join(soup.get_text(separator=" ").split())
        except Exception:
            return ""

    base = domain if domain.startswith("http") else f"https://{domain}"
    homepage = _fetch_text(base)

    # If homepage is very short, also grab /about
    about = ""
    if len(homepage) < 1500:
        about = _fetch_text(base.rstrip("/") + "/about")

    combined = (homepage + " " + about).strip()
    return combined[:MAX_CONTENT_CHARS]


def extract_icp(domain: str) -> dict:
    """
    Given a domain (e.g. 'apollo.io'), scrape the site, then return:
      {
        "problem": str,
        "solution": str,
        "icp_hypotheses": [str, str, str],
        "buying_triggers": [str, str, str],
        "company_name": str,
        "description": str
      }
    """
    # Scrape website content first
    site_content = _scrape_domain(domain)

    if site_content:
        site_section = f"""
Real website content scraped from {domain}:
---
{site_content}
---
Use this content as your primary source. Do NOT invent information not supported by the text above.
"""
    else:
        site_section = f"""
(Website could not be fetched — use your general knowledge about {domain} carefully,
and be explicit that this is inferred, not from live content.)
"""

    prompt = f"""You are a B2B go-to-market analyst.{site_section}
Based on the content above, extract a structured ICP (Ideal Customer Profile) analysis for the company at domain "{domain}".

Rules:
- Derive company_name, description, problem, and solution directly from the website text.
- If the site text is clear, quote or closely paraphrase it — do not substitute generic SaaS copy.
- icp_hypotheses should name the buyer role, company size, and industry in concrete terms.
- buying_triggers should be specific events or signals (e.g. "just raised Series A", "hiring SDRs").

Return ONLY valid JSON with these exact keys:
{{
  "company_name": "short brand name",
  "description": "one-sentence company description",
  "problem": "the core pain the product solves for customers (2-3 sentences)",
  "solution": "how the product addresses that pain (2-3 sentences)",
  "icp_hypotheses": [
    "ICP hypothesis 1: role/company-size/industry description",
    "ICP hypothesis 2: role/company-size/industry description",
    "ICP hypothesis 3: role/company-size/industry description"
  ],
  "buying_triggers": [
    "Trigger 1: specific event or signal indicating buying intent",
    "Trigger 2: specific event or signal indicating buying intent",
    "Trigger 3: specific event or signal indicating buying intent"
  ]
}}

Domain: {domain}"""

    resp = _bedrock().invoke_model(
        modelId=BEDROCK_MODEL,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
        ),
        contentType="application/json",
        accept="application/json",
    )
    raw = json.loads(resp["body"].read())
    text = raw["content"][0]["text"].strip()

    # Extract JSON from the response (handle markdown code blocks)
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        text = json_match.group(0)

    return json.loads(text)

"""
ICP extractor — uses Bedrock Claude to extract ICP profile from a domain name.
"""

import json
import re
import boto3

BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION = "us-west-2"

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


def extract_icp(domain: str) -> dict:
    """
    Given a domain (e.g. 'apollo.io'), return:
      {
        "problem": str,
        "solution": str,
        "icp_hypotheses": [str, str, str],
        "buying_triggers": [str, str, str],
        "company_name": str,
        "description": str
      }
    """
    prompt = f"""You are a B2B go-to-market analyst. Given the SaaS company domain "{domain}", extract a structured ICP (Ideal Customer Profile) analysis.

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

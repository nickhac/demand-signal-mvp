"""
Demand Signal MVP — domain-in to 5 opportunity cards with paywall.
Stack: FastAPI + Jinja2 + Bedrock (Claude) + mock Apify layer.
"""

import json
import os
import uuid
from pathlib import Path
from typing import Optional

import boto3
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from icp_extractor import extract_icp
from opportunity_engine import build_opportunities

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="Demand Signal MVP")
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# In-memory session store (good enough for MVP demo)
SESSIONS: dict[str, dict] = {}

FREE_CARD_LIMIT = 5

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(request: Request, domain: str = Form(...)):
    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not domain:
        return templates.TemplateResponse(
            request, "index.html", {"error": "Please enter a domain."}
        )

    # Step 1: LLM extracts ICP profile from domain
    try:
        icp = extract_icp(domain)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "index.html",
            {"error": f"ICP extraction failed: {exc}"},
        )

    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "domain": domain,
        "icp": icp,
        "confirmed": False,
    }

    return templates.TemplateResponse(
        request, "icp_confirm.html",
        {"session_id": session_id, "domain": domain, "icp": icp},
    )


@app.post("/confirm-icp", response_class=HTMLResponse)
async def confirm_icp(
    request: Request,
    session_id: str = Form(...),
    problem: str = Form(...),
    solution: str = Form(...),
    icp_1: str = Form(...),
    icp_2: str = Form(...),
    icp_3: str = Form(...),
    trigger_1: str = Form(...),
    trigger_2: str = Form(...),
    trigger_3: str = Form(...),
):
    if session_id not in SESSIONS:
        return RedirectResponse("/", status_code=303)

    # Update ICP with any edits
    SESSIONS[session_id]["icp"].update(
        {
            "problem": problem,
            "solution": solution,
            "icp_hypotheses": [h for h in [icp_1, icp_2, icp_3] if h.strip()],
            "buying_triggers": [t for t in [trigger_1, trigger_2, trigger_3] if t.strip()],
        }
    )
    SESSIONS[session_id]["confirmed"] = True

    # Step 2: Build opportunity cards
    domain = SESSIONS[session_id]["domain"]
    icp = SESSIONS[session_id]["icp"]

    try:
        cards = build_opportunities(domain, icp)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "icp_confirm.html",
            {
                "session_id": session_id,
                "domain": domain,
                "icp": icp,
                "error": f"Opportunity search failed: {exc}",
            },
        )

    SESSIONS[session_id]["cards"] = cards

    return templates.TemplateResponse(
        request, "results.html",
        {
            "session_id": session_id,
            "domain": domain,
            "icp": icp,
            "cards": cards[:FREE_CARD_LIMIT],
            "total_cards": len(cards),
            "free_limit": FREE_CARD_LIMIT,
            "show_paywall": len(cards) >= FREE_CARD_LIMIT,
        },
    )


@app.post("/rate-card")
async def rate_card(request: Request):
    body = await request.json()
    session_id = body.get("session_id")
    card_index = body.get("card_index")
    rating = body.get("rating")

    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")

    cards = SESSIONS[session_id].get("cards", [])
    if 0 <= card_index < len(cards):
        cards[card_index]["user_rating"] = rating

    return JSONResponse({"ok": True})


@app.get("/paywall", response_class=HTMLResponse)
async def paywall(request: Request, session_id: str = ""):
    domain = SESSIONS.get(session_id, {}).get("domain", "")
    return templates.TemplateResponse(
        request, "paywall.html",
        {"session_id": session_id, "domain": domain},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}

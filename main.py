"""
Demand Signal MVP — domain-in to 5 opportunity cards with paywall.
Stack: FastAPI + Jinja2 + Bedrock (Claude) + Apify Reddit signals.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Optional

import boto3
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
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

# ── Background job runner ─────────────────────────────────────────────────────

async def _run_opportunity_job(session_id: str) -> None:
    """Background task: fetch signals + rank cards, store result in session."""
    session = SESSIONS.get(session_id)
    if not session:
        return
    domain = session["domain"]
    icp = session["icp"]
    try:
        cards = await asyncio.to_thread(build_opportunities, domain, icp)
        SESSIONS[session_id]["cards"] = cards
        SESSIONS[session_id]["job_status"] = "done"
    except Exception as exc:
        SESSIONS[session_id]["job_status"] = "error"
        SESSIONS[session_id]["job_error"] = str(exc)


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

    # Step 1: LLM extracts ICP profile from domain (run in thread pool — boto3 is sync)
    try:
        icp = await asyncio.to_thread(extract_icp, domain)
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
    background_tasks: BackgroundTasks,
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
    SESSIONS[session_id]["job_status"] = "running"

    # Kick off Apify + LLM work in background (avoids HTTP timeout)
    background_tasks.add_task(_run_opportunity_job, session_id)

    domain = SESSIONS[session_id]["domain"]
    return templates.TemplateResponse(
        request, "loading.html",
        {"session_id": session_id, "domain": domain},
    )


@app.get("/status/{session_id}")
async def job_status(session_id: str):
    """Polling endpoint — returns job state as JSON."""
    session = SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({"status": session.get("job_status", "running")})


@app.get("/results/{session_id}", response_class=HTMLResponse)
async def results(request: Request, session_id: str):
    """Show results once the background job is done."""
    session = SESSIONS.get(session_id)
    if not session:
        return RedirectResponse("/", status_code=303)

    job_status = session.get("job_status", "running")
    if job_status == "running":
        domain = session["domain"]
        return templates.TemplateResponse(
            request, "loading.html",
            {"session_id": session_id, "domain": domain},
        )
    if job_status == "error":
        return templates.TemplateResponse(
            request, "icp_confirm.html",
            {
                "session_id": session_id,
                "domain": session["domain"],
                "icp": session["icp"],
                "error": f"Opportunity search failed: {session.get('job_error', 'unknown error')}",
            },
        )

    cards = session.get("cards", [])
    return templates.TemplateResponse(
        request, "results.html",
        {
            "session_id": session_id,
            "domain": session["domain"],
            "icp": session["icp"],
            "cards": cards[:FREE_CARD_LIMIT],
            "total_cards": len(cards),
            "free_limit": FREE_CARD_LIMIT,
            "show_paywall": len(cards) >= FREE_CARD_LIMIT,
        },
    )


@app.post("/rate")
async def rate_card(request: Request, session_id: str = Form(...), card_rank: int = Form(...), rating: int = Form(...)):
    """Save star rating for a card."""
    session = SESSIONS.get(session_id)
    if session and "cards" in session:
        for card in session["cards"]:
            if card.get("rank") == card_rank:
                card["user_rating"] = rating
                break
    return JSONResponse({"ok": True})


@app.get("/paywall", response_class=HTMLResponse)
async def paywall(request: Request):
    return templates.TemplateResponse(request, "paywall.html")

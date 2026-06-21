"""
Demand Signal MVP — domain-in to 5 opportunity cards with paywall.
Stack: FastAPI + Jinja2 + Bedrock (Claude) + Apify Reddit signals.
"""

import asyncio
import json
import logging
import os
import time
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

START_TIME = time.time()

# Error log path
ERROR_LOG = Path("/tmp/demand-signal-errors.log")

# ── Session persistence helpers ───────────────────────────────────────────────

SESSIONS_DIR = Path("/tmp/sessions")
SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _session_path(session_id: str) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{session_id}.json"


def _save_session(session_id: str, data: dict) -> None:
    """Write session dict to disk."""
    try:
        _session_path(session_id).write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass  # best-effort; in-memory fallback still works


def _load_session(session_id: str) -> Optional[dict]:
    """Read session from disk. Returns None if missing or expired."""
    path = _session_path(session_id)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data
    except Exception:
        return None


def _purge_old_sessions() -> None:
    """Delete session files older than SESSION_TTL_SECONDS."""
    try:
        cutoff = time.time() - SESSION_TTL_SECONDS
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def _get_session(session_id: str) -> Optional[dict]:
    """Return session from in-memory store, falling back to disk."""
    if session_id in SESSIONS:
        return SESSIONS[session_id]
    data = _load_session(session_id)
    if data is not None:
        SESSIONS[session_id] = data
    return data


# In-memory session store (populated lazily from disk on cold start)
SESSIONS: dict[str, dict] = {}

FREE_CARD_LIMIT = 5


# ── Startup: purge old sessions ───────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    _purge_old_sessions()


# ── Background job runner ─────────────────────────────────────────────────────

async def _run_opportunity_job(session_id: str) -> None:
    """Background task: fetch signals + rank cards, store result in session."""
    session = _get_session(session_id)
    if not session:
        return
    domain = session["domain"]
    icp = session["icp"]
    try:
        cards = await asyncio.to_thread(build_opportunities, domain, icp)
        SESSIONS[session_id]["cards"] = cards
        SESSIONS[session_id]["job_status"] = "done"
        _save_session(session_id, SESSIONS[session_id])
    except Exception as exc:
        err_msg = str(exc)
        SESSIONS[session_id]["job_status"] = "error"
        SESSIONS[session_id]["job_error"] = err_msg
        _save_session(session_id, SESSIONS[session_id])
        # Log to error file
        try:
            with ERROR_LOG.open("a") as f:
                f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] "
                        f"session={session_id} domain={domain} error={err_msg}\n")
        except Exception:
            pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/status")
async def app_status():
    """Health / status endpoint — returns app version and uptime."""
    uptime_seconds = int(time.time() - START_TIME)
    return JSONResponse({
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": uptime_seconds,
    })


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
    session_data = {
        "domain": domain,
        "icp": icp,
        "confirmed": False,
    }
    SESSIONS[session_id] = session_data
    _save_session(session_id, session_data)

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
    session = _get_session(session_id)
    if session is None:
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
    _save_session(session_id, SESSIONS[session_id])

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
    session = _get_session(session_id)
    if not session:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({"status": session.get("job_status", "running")})


@app.get("/results/{session_id}", response_class=HTMLResponse)
async def results(request: Request, session_id: str):
    """Show results once the background job is done."""
    session = _get_session(session_id)
    if not session:
        return RedirectResponse("/", status_code=303)

    job_status_val = session.get("job_status", "running")
    if job_status_val == "running":
        domain = session["domain"]
        return templates.TemplateResponse(
            request, "loading.html",
            {"session_id": session_id, "domain": domain},
        )
    if job_status_val == "error":
        return templates.TemplateResponse(
            request, "error.html",
            {
                "session_id": session_id,
                "domain": session["domain"],
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
    session = _get_session(session_id)
    if session and "cards" in session:
        for card in session["cards"]:
            if card.get("rank") == card_rank:
                card["user_rating"] = rating
                break
        _save_session(session_id, SESSIONS[session_id])
    return JSONResponse({"ok": True})


@app.get("/paywall", response_class=HTMLResponse)
async def paywall(request: Request):
    return templates.TemplateResponse(request, "paywall.html")

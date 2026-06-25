"""
Unit tests for the server-side CSV export paywall gate (DEM-82).

Tests that /export-csv/{session_id} enforces paid=True at the server layer
and is NOT bypassable by direct HTTP requests.
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Return a TestClient with sessions and DB rooted in tmp_path."""
    from fastapi.testclient import TestClient

    # Redirect session storage to tmp dir so tests don't touch real data
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    db_path = tmp_path / "test.db"

    monkeypatch.setenv("MARK_PAID_SECRET", "test-secret-123")

    import main as app_module
    monkeypatch.setattr(app_module, "SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr(app_module, "SESSIONS_DB_PATH", db_path)
    monkeypatch.setattr(app_module, "MARK_PAID_SECRET", "test-secret-123")
    # Clear in-memory session cache between tests
    app_module.SESSIONS.clear()

    return TestClient(app_module.app)


def _create_done_session(client, monkeypatch, paid: bool = False, email_linked: bool = False):
    """Directly inject a 'done' session into the session store and return its ID."""
    import main as app_module

    session_id = str(uuid.uuid4())
    session_data = {
        "domain": "example.com",
        "icp": {"company_name": "Example", "problem": "x", "solution": "y",
                "icp_hypotheses": [], "buying_triggers": []},
        "confirmed": True,
        "job_status": "done",
        "cards": [
            {
                "person_name": "Alice",
                "company_name": "Acme",
                "person_title": "CEO",
                "pain_summary": "Needs more leads",
                "source_post_url": "https://reddit.com/r/sales/1",
                "why_now": "Q4 push",
                "outreach_pain_first": "Draft 1",
                "outreach_value_give": "Draft 2",
                "outreach_direct_ask": "Draft 3",
            }
        ] * 6,
    }
    if paid:
        session_data["paid"] = True

    app_module.SESSIONS[session_id] = session_data
    session_path = app_module.SESSIONS_DIR / f"{session_id}.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    if email_linked:
        # Write email_sessions record (simulates email capture, NOT payment)
        app_module._init_db()
        conn = app_module._get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO email_sessions "
                "(email, session_id, domain, created_at, results_json) VALUES (?,?,?,?,?)",
                ("user@example.com", session_id, "example.com", "2026-01-01T00:00:00Z", "[]"),
            )
            conn.commit()
        finally:
            conn.close()

    return session_id


class TestExportCsvPaywall:
    def test_unpaid_session_returns_403(self, client, monkeypatch):
        """Direct curl to /export-csv without payment must return HTTP 403."""
        sid = _create_done_session(client, monkeypatch, paid=False)
        resp = client.get(f"/export-csv/{sid}")
        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"] == "Upgrade required"
        assert "paywall_url" in body["detail"]
        assert sid in body["detail"]["paywall_url"]

    def test_email_only_session_returns_403(self, client, monkeypatch):
        """Email capture alone must NOT grant CSV access."""
        sid = _create_done_session(client, monkeypatch, paid=False, email_linked=True)
        resp = client.get(f"/export-csv/{sid}")
        assert resp.status_code == 403

    def test_paid_session_returns_csv(self, client, monkeypatch):
        """Session with paid=True must return HTTP 200 with CSV content."""
        sid = _create_done_session(client, monkeypatch, paid=True)
        resp = client.get(f"/export-csv/{sid}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        lines = resp.text.splitlines()
        assert lines[0].startswith("Prospect Name")
        assert len(lines) > 1  # header + at least one data row

    def test_missing_session_returns_404(self, client, monkeypatch):
        """Unknown session IDs must return 404, not 200."""
        resp = client.get("/export-csv/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_mark_paid_requires_auth(self, client, monkeypatch):
        """mark-paid endpoint must reject missing/wrong token with 401."""
        sid = _create_done_session(client, monkeypatch, paid=False)
        resp = client.post(f"/api/mark-paid/{sid}")
        assert resp.status_code == 401

        resp2 = client.post(f"/api/mark-paid/{sid}", headers={"Authorization": "Bearer wrong"})
        assert resp2.status_code == 401

    def test_mark_paid_unlocks_export(self, client, monkeypatch):
        """After mark-paid, export must succeed."""
        sid = _create_done_session(client, monkeypatch, paid=False)
        # Confirm locked before
        assert client.get(f"/export-csv/{sid}").status_code == 403

        # Mark paid with correct secret
        r = client.post(
            f"/api/mark-paid/{sid}",
            headers={"Authorization": "Bearer test-secret-123"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Now export works
        resp = client.get(f"/export-csv/{sid}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

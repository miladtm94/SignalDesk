from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import Run, RunEvent
from backend.app.services.orchestrator import orchestrator


def test_run_lifecycle_without_external_model_calls(monkeypatch):
    monkeypatch.setattr(orchestrator, "start", lambda run_id: None)
    payload = {
        "ticker": "NVDA",
        "trade_date": "2026-08-25",
        "asset_type": "stock",
        "selected_analysts": ["market"],
        "llm_provider": "ollama",
        "deep_think_llm": "qwen3:latest",
        "quick_think_llm": "qwen3:latest",
        "temperature": None,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "checkpoint_enabled": True,
        "output_language": "English",
    }
    with TestClient(app) as client:
        created = client.post("/api/runs", json=payload)
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        detail = client.get(f"/api/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "queued"

        note = client.post(
            f"/api/runs/{run_id}/notes",
            json={"note_text": "Review after earnings", "tags": ["Catalyst"], "starred": True},
        )
        assert note.status_code == 201
        assert note.json()["tags"] == ["catalyst"]

        removed = client.delete(f"/api/runs/{run_id}")
        assert removed.status_code == 204


def test_provider_response_masks_keys(monkeypatch):
    raw = "sk-test-value-never-returned-7788"
    monkeypatch.setenv("OPENAI_API_KEY", raw)
    with TestClient(app) as client:
        body = client.get("/api/config/providers").text
    assert raw not in body
    assert "7788" in body


def test_terminal_websocket_replays_then_closes_cleanly(monkeypatch):
    monkeypatch.setattr(orchestrator, "start", lambda run_id: None)
    payload = {
        "ticker": "NVDA",
        "trade_date": "2026-08-25",
        "asset_type": "stock",
        "selected_analysts": ["market"],
        "llm_provider": "ollama",
        "deep_think_llm": "qwen3:latest",
        "quick_think_llm": "qwen3:latest",
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "checkpoint_enabled": True,
        "output_language": "English",
    }
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json=payload).json()["run_id"]
        with SessionLocal.begin() as session:
            run = session.get(Run, run_id)
            run.status = "failed"
            session.add(
                RunEvent(
                    run_id=run_id,
                    sequence=1,
                    event_type="failed",
                    payload={"status": "failed", "message": "test"},
                )
            )

        with client.websocket_connect(f"/api/runs/{run_id}/stream") as websocket:
            assert websocket.receive_json()["event_type"] == "failed"

        assert client.delete(f"/api/runs/{run_id}").status_code == 204

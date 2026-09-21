from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import Run, RunEvent
from backend.app.services.orchestrator import orchestrator


def test_health_reports_the_installed_core_version(monkeypatch):
    monkeypatch.setattr(main_module, "version", lambda package: "0.5.0")
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "upstream": "TradingAgents v0.5.0",
    }


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


def test_provider_timeout_message_is_actionable():
    message = orchestrator._failure_message(
        RuntimeError("504 DEADLINE_EXCEEDED: provider timeout")
    )
    assert "timed out" in message
    assert "Resume" in message


def test_interrupted_run_becomes_resumable(monkeypatch):
    monkeypatch.setattr(orchestrator, "start", lambda run_id: None)
    payload = {
        "ticker": "SPCX",
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
        assert orchestrator.recover_interrupted_runs() == 1
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["status"] == "failed"
        assert "Resume" in detail["error_message"]
        assert client.delete(f"/api/runs/{run_id}").status_code == 204

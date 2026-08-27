from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_provider_catalog_distinguishes_supported_configured_and_active(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with TestClient(app) as client:
        providers = client.get("/api/config/providers").json()["providers"]
        by_id = {provider["id"]: provider for provider in providers}

        assert {"openai", "anthropic", "google", "mistral", "ollama"} <= set(by_id)
        assert by_id["mistral"]["configured"] is False
        assert by_id["ollama"]["configured"] is True
        assert by_id["openai"]["key_env"] == "OPENAI_API_KEY"

        languages = client.get("/api/config/providers").json()["output_languages"]
        assert languages[0] == {"label": "English (default)", "value": "English"}
        assert languages[-1]["value"] == "Russian"


def test_custom_model_and_single_active_provider_are_persisted():
    payload = {
        "llm_provider": "ollama",
        "quick_think_llm": "my-fast-model:latest",
        "deep_think_llm": "my-deep-model:latest",
        "custom_models": {
            "ollama": ["my-fast-model:latest", "my-deep-model:latest"]
        },
        "openai_reasoning_effort": "medium",
    }
    with TestClient(app) as client:
        saved = client.put("/api/config/preferences", json=payload)
        assert saved.status_code == 200
        current = client.get("/api/config/preferences").json()

    assert current["llm_provider"] == "ollama"
    assert current["custom_models"]["ollama"] == [
        "my-fast-model:latest",
        "my-deep-model:latest",
    ]
    assert current["openai_reasoning_effort"] == "medium"


def test_unconfigured_provider_cannot_be_made_active(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with TestClient(app) as client:
        response = client.put(
            "/api/config/preferences",
            json={
                "llm_provider": "mistral",
                "quick_think_llm": "mistral-small-latest",
                "deep_think_llm": "mistral-large-latest",
            },
        )

    assert response.status_code == 400
    assert "configured provider" in response.json()["detail"]

from __future__ import annotations

import os
from typing import Any

from backend.adapters.tradingagents_adapter import upstream_catalog


def get_provider_configuration() -> dict[str, Any]:
    catalog = upstream_catalog()
    available = []
    for name, details in catalog["providers"].items():
        key_env = details.get("key_env")
        configured = bool(key_env and os.getenv(key_env))
        availability = "api_key"

        if name == "ollama":
            configured = bool(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
            availability = "local_runtime"
        elif name == "openai_compatible":
            configured = bool(os.getenv("OPENAI_COMPATIBLE_BASE_URL"))
            availability = "custom_endpoint"
        elif name == "bedrock":
            configured = any(
                os.getenv(key)
                for key in ("AWS_BEARER_TOKEN_BEDROCK", "AWS_PROFILE", "AWS_ACCESS_KEY_ID")
            )
            availability = "aws_credentials"

        available.append(
            {
                "id": name,
                "availability": availability,
                "configured": configured,
                "key_env": key_env,
                "quick_models": details["quick"],
                "deep_models": details["deep"],
                "reasoning": details.get("reasoning"),
                "masked_key": f"••••{os.getenv(key_env, '')[-4:]}"
                if key_env and os.getenv(key_env)
                else None,
            }
        )

    return {"providers": available, "defaults": catalog["defaults"]}


def get_vendor_configuration() -> dict[str, Any]:
    return {"vendors": upstream_catalog()["vendors"]}

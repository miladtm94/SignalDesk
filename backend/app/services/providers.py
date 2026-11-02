from __future__ import annotations

import os
from typing import Any

from backend.adapters.tradingagents_adapter import (
    PLAN_PROVIDERS,
    PlanSignInError,
    chatgpt_plan_status,
    claude_plan_status,
    claude_sign_in_cancel,
    claude_sign_in_finish,
    claude_sign_in_start,
    upstream_catalog,
)

__all__ = [
    "PlanSignInError", "claude_sign_in_cancel", "claude_sign_in_finish", "claude_sign_in_start",
    "get_provider_configuration", "get_vendor_configuration", "plan_status",
]


def plan_status(provider: str, fresh: bool = False) -> dict[str, Any]:
    """Sign-in state of a provider billed to the person's own plan."""
    if provider == "claude":
        return claude_plan_status(fresh=fresh)
    return chatgpt_plan_status()


def get_provider_configuration(fresh: bool = False) -> dict[str, Any]:
    catalog = upstream_catalog()
    available = []
    for name, details in catalog["providers"].items():
        key_env = details.get("key_env")
        configured = bool(key_env and os.getenv(key_env))
        availability = "api_key"
        subscription = None

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
        elif name in PLAN_PROVIDERS:
            subscription = plan_status(name, fresh=fresh)
            configured = bool(subscription["ready"])
            availability = "subscription"

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
                "subscription": subscription,
            }
        )

    return {
        "providers": available,
        "output_languages": catalog["output_languages"],
        "defaults": catalog["defaults"],
    }


def get_vendor_configuration() -> dict[str, Any]:
    return {"vendors": upstream_catalog()["vendors"]}

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from ..database import SessionLocal
from ..models import AppPreference
from ..schemas import PreferencesWrite, SecretMask, SecretWrite
from ..services.costs import public_cost_table
from ..services.providers import get_provider_configuration, get_vendor_configuration
from ..services.secrets import secret_store
from ..settings import DATA_DIR

router = APIRouter(prefix="/api/config", tags=["configuration"])


@router.get("/providers")
def providers():
    return get_provider_configuration()


@router.get("/vendors")
def vendors():
    return get_vendor_configuration()


@router.get("/costs")
def costs():
    return public_cost_table()


@router.get("/runtime")
def runtime():
    return {"data_directory": str(DATA_DIR), "localhost_only": True}


@router.get("/preferences", response_model=PreferencesWrite)
def preferences():
    configuration = get_provider_configuration()
    provider_defaults = configuration["defaults"]
    configured = [item for item in configuration["providers"] if item["configured"]]
    active = next(
        (item for item in configured if item["id"] == provider_defaults["llm_provider"]),
        configured[0] if configured else None,
    )
    defaults = PreferencesWrite(
        llm_provider=active["id"] if active else None,
        deep_think_llm=(
            provider_defaults["deep_think_llm"]
            if active and active["id"] == provider_defaults["llm_provider"]
            else active["deep_models"][0]["value"] if active else None
        ),
        quick_think_llm=(
            provider_defaults["quick_think_llm"]
            if active and active["id"] == provider_defaults["llm_provider"]
            else active["quick_models"][0]["value"] if active else None
        ),
        google_thinking_level=provider_defaults.get("google_thinking_level"),
        openai_reasoning_effort=provider_defaults.get("openai_reasoning_effort"),
        anthropic_effort=provider_defaults.get("anthropic_effort"),
    ).model_dump()
    with SessionLocal() as session:
        row = session.get(AppPreference, "defaults")
        if not row:
            return defaults
        # Backfill records created before model defaults were introduced.
        saved = {**defaults, **{key: value for key, value in row.value.items() if value is not None}}
        if not any(item["id"] == saved["llm_provider"] for item in configured):
            saved.update(
                {
                    "llm_provider": defaults["llm_provider"],
                    "quick_think_llm": defaults["quick_think_llm"],
                    "deep_think_llm": defaults["deep_think_llm"],
                }
            )
        return saved


@router.put("/preferences", response_model=PreferencesWrite)
def save_preferences(payload: PreferencesWrite):
    providers = get_provider_configuration()["providers"]
    selected = next((item for item in providers if item["id"] == payload.llm_provider), None)
    if selected is None or not selected["configured"]:
        raise HTTPException(
            status_code=400,
            detail="Choose a configured provider before saving it as active.",
        )
    with SessionLocal.begin() as session:
        row = session.get(AppPreference, "defaults")
        if row:
            row.value = payload.model_dump()
        else:
            session.add(AppPreference(key="defaults", value=payload.model_dump()))
    return payload


@router.get("/secrets", response_model=list[SecretMask])
def list_secrets():
    return secret_store.masks()


@router.put("/secrets/{name}", response_model=SecretMask)
def save_secret(name: str, payload: SecretWrite):
    if payload.name != name:
        raise HTTPException(status_code=400, detail="Secret name in path and body must match.")
    try:
        masked = secret_store.save(name, payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "configured": True, "masked_value": masked}


@router.delete("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_secret(name: str):
    if not secret_store.delete(name):
        raise HTTPException(status_code=404, detail="Secret not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

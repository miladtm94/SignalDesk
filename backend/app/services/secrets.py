from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select

from backend.adapters.tradingagents_adapter import upstream_catalog

from ..database import SessionLocal
from ..models import Secret
from ..settings import DATA_DIR

EXTRA_SECRET_NAMES = {
    "FRED_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "OLLAMA_BASE_URL",
    "OPENAI_COMPATIBLE_BASE_URL",
}


def allowed_secret_names() -> set[str]:
    catalog = upstream_catalog()
    names = {
        provider["key_env"]
        for provider in catalog["providers"].values()
        if provider.get("key_env")
    }
    return names | EXTRA_SECRET_NAMES


class SecretStore:
    def __init__(self, key_path: Path | None = None) -> None:
        self.key_path = key_path or DATA_DIR / ".secrets.key"

    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            self.key_path.chmod(0o600)
        return Fernet(self.key_path.read_bytes().strip())

    def load_into_environment(self) -> None:
        with SessionLocal() as session:
            rows = session.scalars(select(Secret)).all()
        if not rows:
            return
        fernet = self._fernet()
        for row in rows:
            os.environ[row.name] = fernet.decrypt(row.encrypted_value.encode()).decode()

    def save(self, name: str, value: str) -> str:
        if name not in allowed_secret_names():
            raise ValueError("Unsupported secret name")
        encrypted = self._fernet().encrypt(value.encode()).decode()
        with SessionLocal.begin() as session:
            row = session.get(Secret, name)
            if row:
                row.encrypted_value = encrypted
            else:
                session.add(Secret(name=name, encrypted_value=encrypted))
        os.environ[name] = value
        return mask_secret(value)

    def delete(self, name: str) -> bool:
        with SessionLocal.begin() as session:
            row = session.get(Secret, name)
            if row is None:
                return False
            session.delete(row)
        os.environ.pop(name, None)
        return True

    def masks(self) -> list[dict[str, object]]:
        configured_db: set[str]
        with SessionLocal() as session:
            configured_db = set(session.scalars(select(Secret.name)).all())
        result = []
        for name in sorted(allowed_secret_names()):
            raw = os.getenv(name)
            configured = bool(raw) or name in configured_db
            result.append(
                {
                    "name": name,
                    "configured": configured,
                    "masked_value": mask_secret(raw) if raw else ("••••" if configured else None),
                }
            )
        return result


def mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "••••"
    return f"{'•' * min(12, len(value) - 4)}{value[-4:]}"


secret_store = SecretStore()

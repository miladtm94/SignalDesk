from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.getenv("TRADING_CONSOLE_DATA_DIR", BACKEND_DIR / "data")).resolve()
RESULTS_DIR = DATA_DIR / "results"
REPORTS_DIR = Path(
    os.getenv("TRADING_CONSOLE_REPORTS_DIR", PROJECT_DIR / "reports")
).resolve()
CACHE_DIR = DATA_DIR / "checkpoints"
MEMORY_PATH = DATA_DIR / "memory" / "trading_memory.md"

# These must be set before the adapter imports TradingAgents. The upstream
# DEFAULT_CONFIG reads them at import time.
os.environ["TRADINGAGENTS_RESULTS_DIR"] = str(RESULTS_DIR)
os.environ["TRADINGAGENTS_CACHE_DIR"] = str(CACHE_DIR)
os.environ["TRADINGAGENTS_MEMORY_LOG_PATH"] = str(MEMORY_PATH)

for path in (DATA_DIR, RESULTS_DIR, REPORTS_DIR, CACHE_DIR, MEMORY_PATH.parent):
    path.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv(
    "TRADING_CONSOLE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'console.sqlite3'}"
)
HOST = os.getenv("TRADING_CONSOLE_HOST", "127.0.0.1")
PORT = int(os.getenv("TRADING_CONSOLE_PORT", "8765"))
FRONTEND_ORIGINS = tuple(
    value.strip()
    for value in os.getenv(
        "TRADING_CONSOLE_CORS_ORIGINS",
        "http://localhost:5174,http://127.0.0.1:5174,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if value.strip()
)
DAILY_SPEND_CAP_USD = float(os.getenv("TRADING_CONSOLE_DAILY_CAP_USD", "0") or 0)
LLM_REQUEST_TIMEOUT_SECONDS = max(
    30.0, float(os.getenv("TRADING_CONSOLE_LLM_TIMEOUT_SECONDS", "120") or 120)
)
# Google treats 1 as one initial request with no automatic retry. Keeping the
# console default at one prevents a single slow step from doubling its timeout.
LLM_MAX_RETRIES = max(1, int(os.getenv("TRADING_CONSOLE_LLM_MAX_RETRIES", "1") or 1))
RUN_HEARTBEAT_SECONDS = max(
    5.0, float(os.getenv("TRADING_CONSOLE_HEARTBEAT_SECONDS", "15") or 15)
)

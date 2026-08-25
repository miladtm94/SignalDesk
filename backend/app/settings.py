from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("TRADING_CONSOLE_DATA_DIR", BACKEND_DIR / "data")).resolve()
RESULTS_DIR = DATA_DIR / "results"
CACHE_DIR = DATA_DIR / "checkpoints"
MEMORY_PATH = DATA_DIR / "memory" / "trading_memory.md"

# These must be set before the adapter imports TradingAgents. The upstream
# DEFAULT_CONFIG reads them at import time.
os.environ["TRADINGAGENTS_RESULTS_DIR"] = str(RESULTS_DIR)
os.environ["TRADINGAGENTS_CACHE_DIR"] = str(CACHE_DIR)
os.environ["TRADINGAGENTS_MEMORY_LOG_PATH"] = str(MEMORY_PATH)

for path in (DATA_DIR, RESULTS_DIR, CACHE_DIR, MEMORY_PATH.parent):
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

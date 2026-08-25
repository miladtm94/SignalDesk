from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import settings first: it redirects every upstream on-disk path before the
# TradingAgents adapter is imported by the routers below.
from . import settings as settings  # noqa: F401
from .api import config, runs, watchlist
from .database import init_database
from .services.orchestrator import orchestrator
from .services.secrets import secret_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    secret_store.load_into_environment()
    orchestrator.recover_interrupted_runs()
    yield


app = FastAPI(
    title="TradingAgents Research Console",
    description="Local-first research orchestration for TradingAgents. Not financial advice.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.FRONTEND_ORIGINS),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(runs.router)
app.include_router(config.router)
app.include_router(watchlist.router)


@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok", "upstream": "TradingAgents v0.3.1"}

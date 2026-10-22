from __future__ import annotations

from datetime import UTC, date, datetime, time as datetime_time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...adapters.tradingagents_adapter import backtest_dates
from ..database import SessionLocal
from ..models import Backtest, Run, Usage
from ..schemas import BacktestCreate, BacktestCreated, BacktestDetail, BacktestSummary, GridPreview
from ..services.backtests import ACTIVE_STATUSES, BacktestService
from ..services.costs import estimate_run_cost
from ..services.orchestrator import orchestrator
from ..services.portfolio import load_book
from ..services.providers import get_provider_configuration
from ..settings import BACKTEST_MAX_CELLS, DAILY_SPEND_CAP_USD
from .runs import _launch_allowed

router = APIRouter(prefix="/api/backtests", tags=["backtests"])
backtests = BacktestService(orchestrator)


def _grid(start: date, end: date, every: int) -> list[str]:
    try:
        return backtest_dates(start.isoformat(), end.isoformat(), every)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _usage(backtest: Backtest) -> dict[str, Any]:
    """What the sweep has used so far: its cell runs plus the settlement reflections."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Usage).join(Run, Usage.run_id == Run.id).where(Run.backtest_id == backtest.id)
        ).all()
    totals = {
        "llm_calls": sum(row.llm_calls for row in rows),
        "tokens_in": sum(row.tokens_in for row in rows),
        "tokens_out": sum(row.tokens_out for row in rows),
        "estimated_cost_usd": round(sum(row.estimated_cost_usd or 0 for row in rows), 4),
        "api_equivalent_usd": round(sum(row.api_equivalent_usd or 0 for row in rows), 4),
    }
    settlement = ((backtest.summary or {}).get("settlement") or {}).get("usage") or {}
    for key in ("llm_calls", "tokens_in", "tokens_out"):
        totals[key] += int(settlement.get(key, 0))
    totals["api_equivalent_usd"] = round(totals["api_equivalent_usd"] + settlement.get("api_equivalent_usd", 0), 4)
    return totals


def _serialize(backtest: Backtest, detail: bool = False) -> dict[str, Any]:
    cells = backtest.cells
    progress = dict.fromkeys(("queued", "running", "completed", "failed", "stopped"), 0)
    for cell in cells:
        progress[cell.status] = progress.get(cell.status, 0) + 1
    progress["total"] = len(cells)
    progress["scored"] = sum(1 for cell in cells if not cell.pending and cell.alpha_return is not None)
    summary = backtest.summary or {}
    output = {
        "id": backtest.id,
        "name": backtest.name,
        "tickers": backtest.tickers,
        "start_date": backtest.start_date,
        "end_date": backtest.end_date,
        "every_n_days": backtest.every_n_days,
        "dates": backtest.dates,
        "asset_type": backtest.asset_type,
        "selected_analysts": backtest.selected_analysts,
        "llm_provider": backtest.llm_provider,
        "deep_think_llm": backtest.deep_think_llm,
        "quick_think_llm": backtest.quick_think_llm,
        "status": backtest.status,
        "error_message": backtest.error_message,
        "estimated_cost_usd": backtest.estimated_cost_usd,
        "used_portfolio": backtest.portfolio_snapshot is not None,
        "created_at": backtest.created_at,
        "started_at": backtest.started_at,
        "completed_at": backtest.completed_at,
        "settled_at": backtest.settled_at,
        "progress": progress,
        "scores": {key: summary.get(key) for key in ("upstream", "console", "settlement")} if summary else None,
        "usage": _usage(backtest),
    }
    if detail:
        output.update(
            config_snapshot=backtest.config_snapshot,
            portfolio_snapshot=backtest.portfolio_snapshot,
            cells=cells,
        )
    return output


def _load(backtest_id: str) -> Backtest:
    with SessionLocal() as session:
        backtest = session.scalar(
            select(Backtest).options(selectinload(Backtest.cells)).where(Backtest.id == backtest_id)
        )
    if backtest is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return backtest


@router.get("/grid", response_model=GridPreview)
def grid(
    start_date: date,
    end_date: date,
    every_n_days: int = Query(default=7, ge=1, le=365),
    tickers: int = Query(default=1, ge=1, le=12),
) -> dict:
    """The analysis dates a grid would run: from the start, every N days, never past today."""
    dates = _grid(start_date, end_date, every_n_days)
    return {"dates": dates, "cells": len(dates) * tickers, "max_cells": BACKTEST_MAX_CELLS}


@router.post("", response_model=BacktestCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_backtest(payload: BacktestCreate, request: Request) -> dict:
    client = request.client.host if request.client else "local"
    if not _launch_allowed(client):
        raise HTTPException(status_code=429, detail="Launch limit reached; try again in a minute.")
    configured = {item["id"] for item in get_provider_configuration()["providers"] if item["configured"]}
    if payload.llm_provider not in configured:
        raise HTTPException(status_code=400, detail="The selected provider is not configured on the server.")
    if "custom" in (payload.quick_think_llm, payload.deep_think_llm):
        raise HTTPException(status_code=400, detail="Enter the exact custom model ID before launch.")
    dates = _grid(payload.start_date, payload.end_date, payload.every_n_days)
    if not dates:
        raise HTTPException(status_code=400, detail="The grid has no analysis date on or before today.")
    cells = len(dates) * len(payload.tickers)
    if cells > BACKTEST_MAX_CELLS:
        raise HTTPException(
            status_code=400,
            detail=(f"This grid has {cells} cells, each a full multi-agent run; the limit is "
                    f"{BACKTEST_MAX_CELLS}. Widen the step, shorten the range or drop a ticker."),
        )
    portfolio = None
    if payload.use_portfolio:
        portfolio, _ = load_book()
        if portfolio is None:
            raise HTTPException(status_code=400, detail="Save your holdings before deciding against them.")
    if DAILY_SPEND_CAP_USD > 0:
        per_run = estimate_run_cost(
            provider=payload.llm_provider, quick_model=payload.quick_think_llm,
            deep_model=payload.deep_think_llm, analysts=len(payload.selected_analysts),
            debate_rounds=payload.max_debate_rounds, risk_rounds=payload.max_risk_discuss_rounds,
        )
        today = datetime.combine(date.today(), datetime_time.min, tzinfo=UTC)
        with SessionLocal() as session:
            spent = sum(
                value or 0
                for value in session.scalars(
                    select(Usage.estimated_cost_usd).join(Run).where(Run.created_at >= today)
                ).all()
            )
        if spent + per_run * cells > DAILY_SPEND_CAP_USD:
            raise HTTPException(
                status_code=402,
                detail=f"The estimated backtest cost exceeds the ${DAILY_SPEND_CAP_USD:.2f} daily cap.",
            )
    backtest_id, estimate = backtests.create(payload, dates, portfolio)
    return {"backtest_id": backtest_id, "status": "queued", "cells": cells, "estimated_cost_usd": estimate}


@router.get("", response_model=list[BacktestSummary])
def list_backtests() -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Backtest).options(selectinload(Backtest.cells)).order_by(Backtest.created_at.desc())
        ).all()
    return [_serialize(row) for row in rows]


@router.get("/{backtest_id}", response_model=BacktestDetail)
def get_backtest(backtest_id: str) -> dict:
    return _serialize(_load(backtest_id), detail=True)


@router.post("/{backtest_id}/stop", response_model=BacktestSummary)
def stop_backtest(backtest_id: str) -> dict:
    _load(backtest_id)
    if not backtests.request_stop(backtest_id):
        raise HTTPException(status_code=409, detail="Only a queued or running backtest can be stopped.")
    return _serialize(_load(backtest_id))


@router.post("/{backtest_id}/resume", response_model=BacktestSummary, status_code=status.HTTP_202_ACCEPTED)
async def resume_backtest(backtest_id: str) -> dict:
    _load(backtest_id)
    if not backtests.resume(backtest_id):
        raise HTTPException(status_code=409, detail="Nothing to resume: every cell has completed, or a job is running.")
    return _serialize(_load(backtest_id))


@router.post("/{backtest_id}/settle", response_model=BacktestSummary, status_code=status.HTTP_202_ACCEPTED)
async def settle_backtest(backtest_id: str) -> dict:
    _load(backtest_id)
    if not backtests.settle(backtest_id):
        raise HTTPException(status_code=409, detail="Wait for the running job to finish before settling.")
    return _serialize(_load(backtest_id))


@router.delete("/{backtest_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backtest(backtest_id: str) -> Response:
    outcome = backtests.delete(backtest_id)
    if outcome is None:
        raise HTTPException(status_code=409, detail="Stop the backtest before deleting it.")
    if outcome is False:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router", "backtests", "ACTIVE_STATUSES"]

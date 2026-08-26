from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...adapters.tradingagents_adapter import load_current_market_chart
from ..database import SessionLocal
from ..models import Decision, Run, RunEvent, RunNote, Usage
from ..schemas import (
    NoteCreate,
    NoteOut,
    RunChartOut,
    RunCreate,
    RunCreated,
    RunDetail,
    RunPatch,
    RunSummary,
)
from ..services.charts import strategy_levels
from ..services.costs import estimate_run_cost
from ..services.orchestrator import orchestrator
from ..services.providers import get_provider_configuration
from ..settings import DAILY_SPEND_CAP_USD

router = APIRouter(prefix="/api/runs", tags=["runs"])
logger = logging.getLogger(__name__)
_launches: dict[str, deque[float]] = defaultdict(deque)


def _launch_allowed(client_key: str) -> bool:
    now = time.monotonic()
    bucket = _launches[client_key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= 3:
        return False
    bucket.append(now)
    return True


def _run_query():
    return select(Run).options(
        selectinload(Run.sections),
        selectinload(Run.decision),
        selectinload(Run.usage),
        selectinload(Run.notes),
        selectinload(Run.events),
    )


def _serialize_run(run: Run, detail: bool = False) -> dict:
    output = {
        "id": run.id,
        "ticker": run.ticker,
        "trade_date": run.trade_date,
        "asset_type": run.asset_type,
        "selected_analysts": run.selected_analysts,
        "llm_provider": run.llm_provider,
        "deep_think_llm": run.deep_think_llm,
        "quick_think_llm": run.quick_think_llm,
        "temperature": run.temperature,
        "max_debate_rounds": run.max_debate_rounds,
        "max_risk_discuss_rounds": run.max_risk_discuss_rounds,
        "checkpoint_enabled": run.checkpoint_enabled,
        "output_language": run.output_language,
        "estimated_cost_usd": run.estimated_cost_usd,
        "status": run.status,
        "error_message": run.error_message,
        "starred": run.starred,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "decision": run.decision,
        "usage": run.usage,
    }
    if detail:
        output.update(
            {
                "benchmark_ticker": run.benchmark_ticker,
                "config_snapshot": run.config_snapshot,
                "sections": sorted(run.sections, key=lambda item: item.created_at),
                "notes": sorted(run.notes, key=lambda item: item.created_at, reverse=True),
                "events": sorted(run.events, key=lambda item: item.sequence),
            }
        )
    return output


@router.post("", response_model=RunCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_run(payload: RunCreate, request: Request) -> RunCreated:
    client = request.client.host if request.client else "local"
    if not _launch_allowed(client):
        raise HTTPException(status_code=429, detail="Run launch limit reached; try again in a minute.")
    available = {item["id"] for item in get_provider_configuration()["providers"]}
    if payload.llm_provider not in available:
        raise HTTPException(
            status_code=400,
            detail="The selected provider is not configured on the server.",
        )
    if payload.quick_think_llm == "custom" or payload.deep_think_llm == "custom":
        raise HTTPException(status_code=400, detail="Enter the exact custom model ID before launch.")

    estimate = estimate_run_cost(
        provider=payload.llm_provider,
        quick_model=payload.quick_think_llm,
        deep_model=payload.deep_think_llm,
        analysts=len(payload.selected_analysts),
        debate_rounds=payload.max_debate_rounds,
        risk_rounds=payload.max_risk_discuss_rounds,
    )
    if DAILY_SPEND_CAP_USD > 0:
        today = datetime.combine(date.today(), datetime_time.min, tzinfo=timezone.utc)
        with SessionLocal() as session:
            spent = sum(
                value or 0
                for value in session.scalars(
                    select(Usage.estimated_cost_usd)
                    .join(Run)
                    .where(Run.created_at >= today)
                ).all()
            )
        if spent + estimate > DAILY_SPEND_CAP_USD:
            raise HTTPException(
                status_code=402,
                detail=f"Estimated run cost exceeds the ${DAILY_SPEND_CAP_USD:.2f} daily cap.",
            )
    run_id, estimate = orchestrator.create(payload)
    return RunCreated(run_id=run_id, status="queued", estimated_cost_usd=estimate)


@router.get("", response_model=list[RunSummary])
def list_runs(
    ticker: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    rating: str | None = None,
    action: str | None = None,
    starred: bool | None = None,
    tag: str | None = None,
    sort: str = Query(default="created_at", pattern="^(created_at|trade_date|ticker|rating|action)$"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> list[dict]:
    query = _run_query()
    if ticker:
        query = query.where(Run.ticker == ticker.strip().upper())
    if date_from:
        query = query.where(Run.trade_date >= date_from)
    if date_to:
        query = query.where(Run.trade_date <= date_to)
    if starred is not None:
        query = query.where(Run.starred == starred)
    if rating or action or sort in {"rating", "action"}:
        query = query.outerjoin(Decision)
    if rating:
        query = query.where(Decision.rating == rating)
    if action:
        query = query.where(Decision.action == action)
    columns = {
        "created_at": Run.created_at,
        "trade_date": Run.trade_date,
        "ticker": Run.ticker,
        "rating": Decision.rating,
        "action": Decision.action,
    }
    order = columns[sort].asc() if direction == "asc" else columns[sort].desc()
    with SessionLocal() as session:
        rows = session.scalars(query.order_by(order)).unique().all()
        if tag:
            needle = tag.strip().lower()
            rows = [row for row in rows if any(needle in note.tags for note in row.notes)]
        return [_serialize_run(row) for row in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str) -> dict:
    with SessionLocal() as session:
        run = session.scalar(_run_query().where(Run.id == run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return _serialize_run(run, detail=True)


@router.get("/{run_id}/chart", response_model=RunChartOut)
def get_run_chart(run_id: str) -> dict:
    with SessionLocal() as session:
        run = session.scalar(_run_query().where(Run.id == run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        ticker = run.ticker
        levels = strategy_levels(run.decision, run.sections)
    try:
        market = load_current_market_chart(ticker)
    except Exception as exc:
        logger.warning("Current chart unavailable for run %s: %s", run_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Current market candles are temporarily unavailable. The saved report is unaffected.",
        ) from exc
    return {
        "symbol": market.symbol,
        "as_of": market.as_of,
        "current_price": market.candles[-1]["close"],
        "candles": market.candles,
        "levels": levels,
    }


@router.patch("/{run_id}", response_model=RunSummary)
def patch_run(run_id: str, payload: RunPatch) -> dict:
    with SessionLocal.begin() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run.starred = payload.starred
    with SessionLocal() as session:
        run = session.scalar(_run_query().where(Run.id == run_id))
        return _serialize_run(run)


@router.post("/{run_id}/resume", response_model=RunCreated, status_code=status.HTTP_202_ACCEPTED)
async def resume_run(run_id: str) -> RunCreated:
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        estimate = run.estimated_cost_usd
    if not orchestrator.resume(run_id):
        raise HTTPException(status_code=409, detail="Only failed or completed runs can be resumed.")
    return RunCreated(run_id=run_id, status="queued", estimated_cost_usd=estimate)


@router.get("/{run_id}/report.md")
def get_report(run_id: str) -> Response:
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if not run.report_path:
            raise HTTPException(status_code=409, detail="The complete report is not available yet.")
        report_path = Path(run.report_path)
    if not report_path.is_file():
        raise HTTPException(status_code=404, detail="Stored report file is missing")
    return Response(
        report_path.read_text(encoding="utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{run.ticker}-{run.trade_date}.md"'},
    )


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: str) -> Response:
    with SessionLocal.begin() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status == "running":
            raise HTTPException(status_code=409, detail="A running analysis cannot be deleted.")
        session.delete(run)
    orchestrator.delete_report_tree(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{run_id}/notes", response_model=list[NoteOut])
def list_notes(run_id: str) -> list[RunNote]:
    with SessionLocal() as session:
        if session.get(Run, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return list(
            session.scalars(
                select(RunNote).where(RunNote.run_id == run_id).order_by(RunNote.created_at.desc())
            ).all()
        )


@router.post("/{run_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(run_id: str, payload: NoteCreate) -> RunNote:
    with SessionLocal.begin() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        note = RunNote(run_id=run_id, **payload.model_dump())
        session.add(note)
        if payload.starred:
            run.starred = True
        session.flush()
        session.refresh(note)
        return note


@router.websocket("/{run_id}/stream")
async def stream_run(websocket: WebSocket, run_id: str) -> None:
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        if run is None:
            await websocket.close(code=4404)
            return
    await websocket.accept()
    queue = await orchestrator.broadcaster.subscribe(run_id)
    try:
        with SessionLocal() as session:
            events = session.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
            ).all()
            for event in events:
                await websocket.send_json(
                    {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    }
                )
            current = session.get(Run, run_id)
            terminal = current.status in {"completed", "failed"}
        if terminal:
            return
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event["event_type"] in {"completed", "failed"}:
                return
    except WebSocketDisconnect:
        pass
    finally:
        await orchestrator.broadcaster.unsubscribe(run_id, queue)

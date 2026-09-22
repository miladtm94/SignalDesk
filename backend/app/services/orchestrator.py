from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from backend.adapters.tradingagents_adapter import SectionEvent, run_streaming_analysis

from ..database import SessionLocal
from ..models import Decision, Run, RunEvent, RunSection, Usage
from ..schemas import RunCreate
from ..settings import REPORTS_DIR, RUN_HEARTBEAT_SECONDS
from .costs import actual_usage_cost, estimate_run_cost

logger = logging.getLogger(__name__)


class RunBroadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            targets = tuple(self._subscribers.get(run_id, ()))
        for queue in targets:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)


class RunOrchestrator:
    def __init__(self) -> None:
        self.broadcaster = RunBroadcaster()
        self._tasks: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._run_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def create(self, payload: RunCreate) -> tuple[str, float]:
        estimate = estimate_run_cost(
            provider=payload.llm_provider,
            quick_model=payload.quick_think_llm,
            deep_model=payload.deep_think_llm,
            analysts=len(payload.selected_analysts),
            debate_rounds=payload.max_debate_rounds,
            risk_rounds=payload.max_risk_discuss_rounds,
        )
        run_id = str(uuid.uuid4())
        config_snapshot = payload.model_dump(mode="json")
        with SessionLocal.begin() as session:
            session.add(
                Run(
                    id=run_id,
                    ticker=payload.ticker,
                    trade_date=payload.trade_date,
                    asset_type=payload.asset_type,
                    selected_analysts=payload.selected_analysts,
                    llm_provider=payload.llm_provider,
                    deep_think_llm=payload.deep_think_llm,
                    quick_think_llm=payload.quick_think_llm,
                    temperature=payload.temperature,
                    max_debate_rounds=payload.max_debate_rounds,
                    max_risk_discuss_rounds=payload.max_risk_discuss_rounds,
                    checkpoint_enabled=payload.checkpoint_enabled,
                    output_language=payload.output_language,
                    benchmark_ticker=payload.benchmark_ticker,
                    config_snapshot=config_snapshot,
                    estimated_cost_usd=estimate,
                    status="queued",
                )
            )
            session.add(Usage(run_id=run_id))
        self.start(run_id)
        return run_id, estimate

    def start(self, run_id: str) -> None:
        self._loop = asyncio.get_running_loop()
        task = asyncio.create_task(self._run(run_id), name=f"trading-run-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, run_id: str) -> None:
        worker = asyncio.create_task(asyncio.to_thread(self._execute_sync, run_id))
        while not worker.done():
            done, _ = await asyncio.wait({worker}, timeout=RUN_HEARTBEAT_SECONDS)
            if done:
                break
            await self.broadcaster.publish(
                run_id,
                {
                    "sequence": -1,
                    "event_type": "heartbeat",
                    "payload": {"status": "running"},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        await worker

    def recover_interrupted_runs(self) -> int:
        """Make runs orphaned by a prior backend process safely resumable."""
        message = (
            "The backend stopped before this run completed. "
            "Resume the run to continue from its saved checkpoint."
        )
        with SessionLocal.begin() as session:
            rows = session.scalars(
                select(Run).where(Run.status.in_({"queued", "running"}))
            ).all()
            run_ids = [run.id for run in rows]
            for run in rows:
                run.status = "failed"
                run.error_message = message
                run.completed_at = datetime.now(timezone.utc)
        for run_id in run_ids:
            self._record_and_publish(
                run_id,
                "failed",
                {"status": "failed", "message": message, "resumable": True},
            )
        return len(run_ids)

    def _execute_sync(self, run_id: str) -> None:
        lock = self._run_locks[run_id]
        if not lock.acquire(blocking=False):
            return
        try:
            with SessionLocal.begin() as session:
                run = session.get(Run, run_id)
                if run is None:
                    return
                run.status = "running"
                run.error_message = None
                run.started_at = datetime.now(timezone.utc)
                payload = {
                    "run_id": run.id,
                    "ticker": run.ticker,
                    "trade_date": run.trade_date.isoformat(),
                    "asset_type": run.asset_type,
                    "selected_analysts": list(run.selected_analysts),
                    "config_overrides": dict(run.config_snapshot),
                    "report_dir": REPORTS_DIR / run.id,
                }
            self._record_and_publish(run_id, "status", {"status": "running"})

            result = run_streaming_analysis(
                **payload,
                on_section=lambda event, usage: self._on_section(run_id, event, usage),
                on_status=lambda event_type, data: self._record_and_publish(run_id, event_type, data),
            )

            with SessionLocal.begin() as session:
                run = session.get(Run, run_id)
                if run is None:
                    return
                usage = session.get(Usage, run_id)
                if usage:
                    self._apply_usage(usage, run, result.usage)
                run.status = "completed"
                run.completed_at = datetime.now(timezone.utc)
                run.report_path = str(result.report_path)
                decision = session.get(Decision, run_id) or Decision(run_id=run_id)
                decision.rating = result.decision
                self._hydrate_structured_decision(session, run_id, decision)
                session.add(decision)

            self._record_and_publish(
                run_id,
                "completed",
                {"status": "completed", "rating": result.decision, "degraded_streaming": result.degraded_streaming},
            )
        except Exception as exc:
            logger.exception("TradingAgents run %s failed", run_id)
            message = self._failure_message(exc)
            with SessionLocal.begin() as session:
                run = session.get(Run, run_id)
                if run:
                    run.status = "failed"
                    run.error_message = message
                    run.completed_at = datetime.now(timezone.utc)
            self._record_and_publish(run_id, "failed", {"status": "failed", "message": message})
        finally:
            lock.release()

    @staticmethod
    def _failure_message(exc: Exception) -> str:
        raw = str(exc) or type(exc).__name__
        normalized = raw.lower()
        if "deadline_exceeded" in normalized or "timed out" in normalized or "timeout" in normalized:
            return (
                "The model provider timed out before completing this step. "
                "Your completed sections and checkpoint are intact; use Resume to retry."
            )
        return raw[:4000]

    def _on_section(self, run_id: str, event: SectionEvent, usage_snapshot: dict[str, int]) -> None:
        with SessionLocal.begin() as session:
            run = session.get(Run, run_id)
            if run is None:
                return
            row = session.scalar(
                select(RunSection).where(
                    RunSection.run_id == run_id, RunSection.section_key == event.section_key
                )
            )
            if row is None:
                row = RunSection(
                    run_id=run_id,
                    section_key=event.section_key,
                    content_md=event.content_md,
                    structured_json=event.structured_json,
                )
                session.add(row)
            else:
                row.content_md = event.content_md
                row.structured_json = event.structured_json
            usage = session.get(Usage, run_id)
            if usage:
                self._apply_usage(usage, run, usage_snapshot)
        self._record_and_publish(
            run_id,
            "section",
            {
                "section_key": event.section_key,
                "content_md": event.content_md,
                "structured_json": event.structured_json,
                "usage": usage_snapshot,
            },
        )

    @staticmethod
    def _apply_usage(usage: Usage, run: Run, snapshot: dict[str, int]) -> None:
        usage.llm_calls = snapshot.get("llm_calls", 0)
        usage.tool_calls = snapshot.get("tool_calls", 0)
        usage.tokens_in = snapshot.get("tokens_in", 0)
        usage.tokens_out = snapshot.get("tokens_out", 0)
        usage.estimated_cost_usd = actual_usage_cost(
            provider=run.llm_provider,
            quick_model=run.quick_think_llm,
            deep_model=run.deep_think_llm,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
        )

    @staticmethod
    def _hydrate_structured_decision(session, run_id: str, decision: Decision) -> None:
        rows = session.scalars(select(RunSection).where(RunSection.run_id == run_id)).all()
        structured = {row.section_key: row.structured_json for row in rows if row.structured_json}
        pm = structured.get("portfolio_manager") or {}
        trader = structured.get("trader") or {}
        sentiment = structured.get("sentiment_report") or {}
        decision.rating = pm.get("rating", decision.rating)
        decision.price_target = pm.get("price_target")
        decision.time_horizon = pm.get("time_horizon")
        decision.action = trader.get("action")
        decision.entry_price = trader.get("entry_price")
        decision.stop_loss = trader.get("stop_loss")
        decision.confidence = sentiment.get("confidence")

    def _record_and_publish(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with SessionLocal.begin() as session:
            sequence = session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run_id)
            )
            event = RunEvent(
                run_id=run_id,
                sequence=int(sequence or 0) + 1,
                event_type=event_type,
                payload=payload,
            )
            session.add(event)
            session.flush()
            envelope = {
                "sequence": event.sequence,
                "event_type": event_type,
                "payload": payload,
                "created_at": event.created_at.isoformat(),
            }
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcaster.publish(run_id, envelope), self._loop)

    def resume(self, run_id: str) -> bool:
        with SessionLocal.begin() as session:
            run = session.get(Run, run_id)
            if run is None or run.status not in {"failed", "completed"}:
                return False
            run.status = "queued"
            run.error_message = None
            run.completed_at = None
        self.start(run_id)
        return True

    @staticmethod
    def delete_report_tree(run_id: str) -> None:
        target = (REPORTS_DIR / run_id).resolve()
        if target.parent == REPORTS_DIR.resolve() and target.exists():
            shutil.rmtree(target)


orchestrator = RunOrchestrator()

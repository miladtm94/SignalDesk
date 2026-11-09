"""Backtests: the analysis run over a grid of tickers and dates, scored by what came next.

Every cell is an ordinary console run: its sections stream live and its report
reads like any other. All cells of a sweep share a memory log of their own, as
in upstream's ``run_backtest``, so the console's live log is never written to
and a later cell learns only from the sweep's earlier decisions whose outcome
was known by its date. Once the cells have run, each decision whose holding
window has traded is settled (raw return and alpha against the instrument's
benchmark, from the first close after its date) and the sweep is scored by
rating with upstream's ``summarize``.

This evaluates decision quality. It is not a portfolio simulation: a book, when
used, is the same standing book for every cell rather than a position carried
forward.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import fmean
from typing import Any

from sqlalchemy import delete, select

from backend.adapters.tradingagents_adapter import (
    RATING_DIRECTION,
    backtest_benchmark,
    backtest_outcomes,
    backtest_scores,
    settle_backtest,
    sweep_blocker,
)

from ..database import SessionLocal
from ..models import Backtest, BacktestCell, Decision, Run, RunEvent, RunSection, Usage
from ..schemas import BacktestCreate
from .costs import estimate_run_cost
from .orchestrator import RunOrchestrator, backtest_paths

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"queued", "running", "stopping", "settling"})
RETRYABLE_CELLS = frozenset({"queued", "failed", "stopped"})
# Fields of a backtest request that describe the grid rather than one analysis.
_GRID_FIELDS = {"name", "tickers", "start_date", "end_date", "every_n_days", "use_portfolio"}


def _now() -> datetime:
    return datetime.now(UTC)


def score_cells(cells: list[BacktestCell]) -> dict[str, Any]:
    """Console-side figures beside upstream's by-rating score: overall and per ticker.

    Only settled decisions with a readable rating count. A directional call is
    a hit when its alpha has the sign its rating claims; Hold claims none.
    """
    resolved = [
        cell for cell in cells
        if not cell.pending and cell.alpha_return is not None and cell.rating in RATING_DIRECTION
    ]

    def figures(group: list[BacktestCell]) -> dict[str, Any]:
        directional = [cell for cell in group if RATING_DIRECTION[cell.rating]]
        hits = sum(1 for cell in directional if cell.alpha_return * RATING_DIRECTION[cell.rating] > 0)
        return {
            "resolved": len(group),
            "directional": len(directional),
            "hit_rate": hits / len(directional) if directional else None,
            "mean_alpha": fmean(cell.alpha_return for cell in group) if group else None,
        }

    by_ticker: dict[str, list[BacktestCell]] = defaultdict(list)
    for cell in resolved:
        by_ticker[cell.ticker].append(cell)
    return {**figures(resolved), "per_ticker": {ticker: figures(group) for ticker, group in by_ticker.items()}}


class BacktestService:
    def __init__(self, runs: RunOrchestrator) -> None:
        self.runs = runs
        self._tasks: set[asyncio.Task[None]] = set()
        self._active: set[str] = set()
        self._stop: set[str] = set()

    # --- lifecycle ---------------------------------------------------------------------

    def create(self, payload: BacktestCreate, dates: list[str], portfolio: dict[str, Any] | None) -> tuple[str, float]:
        per_run = estimate_run_cost(
            provider=payload.llm_provider,
            quick_model=payload.quick_think_llm,
            deep_model=payload.deep_think_llm,
            analysts=len(payload.selected_analysts),
            debate_rounds=payload.max_debate_rounds,
            risk_rounds=payload.max_risk_discuss_rounds,
        )
        cells = len(payload.tickers) * len(dates)
        estimate = round(per_run * cells, 4)
        backtest_id = str(uuid.uuid4())
        snapshot = payload.model_dump(mode="json")
        with SessionLocal.begin() as session:
            session.add(
                Backtest(
                    id=backtest_id,
                    name=payload.name,
                    tickers=payload.tickers,
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                    every_n_days=payload.every_n_days,
                    dates=dates,
                    asset_type=payload.asset_type,
                    selected_analysts=payload.selected_analysts,
                    llm_provider=payload.llm_provider,
                    deep_think_llm=payload.deep_think_llm,
                    quick_think_llm=payload.quick_think_llm,
                    config_snapshot=snapshot,
                    portfolio_snapshot=portfolio,
                    status="queued",
                    estimated_cost_usd=estimate,
                )
            )
            # Ticker by ticker, date by date, as upstream sweeps: each ticker's
            # later cells can learn from its earlier ones.
            position = 0
            for ticker in payload.tickers:
                benchmark = backtest_benchmark(ticker, snapshot)
                for value in dates:
                    session.add(
                        BacktestCell(
                            backtest_id=backtest_id,
                            position=position,
                            ticker=ticker,
                            trade_date=date.fromisoformat(value),
                            status="queued",
                            benchmark=benchmark,
                        )
                    )
                    position += 1
        backtest_paths(backtest_id)[0].mkdir(parents=True, exist_ok=True)
        self._launch(backtest_id, self._sweep(backtest_id))
        return backtest_id, estimate

    def _launch(self, backtest_id: str, job) -> None:
        self.runs.bind_loop()
        self._active.add(backtest_id)
        task = asyncio.create_task(job, name=f"backtest-{backtest_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def is_active(self, backtest_id: str) -> bool:
        return backtest_id in self._active

    def resume(self, backtest_id: str) -> bool:
        """Run the cells that have not completed, then settle; False when there is nothing to do."""
        if self.is_active(backtest_id):
            return False
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest is None or backtest.status in ACTIVE_STATUSES:
                return False
            if not any(cell.status in RETRYABLE_CELLS for cell in backtest.cells):
                return False
            backtest.status = "queued"
            backtest.error_message = None
        self._launch(backtest_id, self._sweep(backtest_id))
        return True

    def settle(self, backtest_id: str) -> bool:
        """Score the decisions whose holding window has traded since; False while a job runs."""
        if self.is_active(backtest_id):
            return False
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest is None or backtest.status in ACTIVE_STATUSES:
                return False
            previous = backtest.status
            backtest.status = "settling"
        self._launch(backtest_id, self._settle_only(backtest_id, previous))
        return True

    def request_stop(self, backtest_id: str) -> bool:
        """Stop after the running cell's current step; the cell is re-run on resume."""
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest is None or backtest.status not in {"queued", "running"}:
                return False
            backtest.status = "stopping"
            running = [cell.run_id for cell in backtest.cells if cell.status == "running" and cell.run_id]
        self._stop.add(backtest_id)
        for run_id in running:
            self.runs.request_stop(run_id)
        return True

    def delete(self, backtest_id: str) -> bool | None:
        """Delete a backtest, its cell runs and its files; None when it is still running."""
        if self.is_active(backtest_id):
            return None
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest is None:
                return False
            if backtest.status in ACTIVE_STATUSES:
                return None
            run_ids = list(session.scalars(select(Run.id).where(Run.backtest_id == backtest_id)).all())
            for run in session.scalars(select(Run).where(Run.backtest_id == backtest_id)).all():
                session.delete(run)
            session.delete(backtest)
        for run_id in run_ids:
            self.runs.delete_report_tree(run_id)
        directory = backtest_paths(backtest_id)[0]
        if directory.exists():
            shutil.rmtree(directory)
        return True

    def recover_interrupted(self) -> int:
        """A backtest a previous backend left mid-flight becomes resumable."""
        message = ("The backend stopped while this backtest ran. Resume it to run the cells "
                   "that had not finished.")
        with SessionLocal.begin() as session:
            rows = session.scalars(select(Backtest).where(Backtest.status.in_(ACTIVE_STATUSES))).all()
            for backtest in rows:
                backtest.status = "stopped"
                backtest.error_message = message
                backtest.completed_at = _now()
                for cell in backtest.cells:
                    if cell.status == "running":
                        cell.status = "stopped"
                        cell.error_message = "Interrupted when the backend stopped."
            return len(rows)

    # --- the jobs ----------------------------------------------------------------------

    async def _sweep(self, backtest_id: str) -> None:
        attempted: set[int] = set()
        try:
            with SessionLocal.begin() as session:
                backtest = session.get(Backtest, backtest_id)
                backtest.status = "running"
                backtest.started_at = backtest.started_at or _now()
                backtest.completed_at = None
            while backtest_id not in self._stop:
                picked = self._next_cell(backtest_id, attempted)
                if picked is None:
                    break
                cell_id, run_id = picked
                attempted.add(cell_id)
                await self.runs.execute(run_id)
                blocker = self._finish_cell(cell_id, run_id)
                await asyncio.to_thread(self._refresh, backtest_id)
                if blocker:
                    self._finish(backtest_id, "stopped", blocker)
                    return
            if backtest_id in self._stop:
                self._finish(backtest_id, "stopped", "Stopped on request. Resume to run the remaining cells.")
                return
            self._set_status(backtest_id, "settling")
            await asyncio.to_thread(self._settle_sync, backtest_id)
            self._finish(backtest_id, "completed", None)
        except Exception as exc:
            logger.exception("Backtest %s failed", backtest_id)
            self._finish(backtest_id, "failed", str(exc)[:4000] or type(exc).__name__)
        finally:
            self._stop.discard(backtest_id)
            self._active.discard(backtest_id)

    async def _settle_only(self, backtest_id: str, previous: str) -> None:
        try:
            await asyncio.to_thread(self._settle_sync, backtest_id)
            with SessionLocal.begin() as session:
                backtest = session.get(Backtest, backtest_id)
                # A sweep that decided every cell and failed only while settling
                # is complete once it settles; any other status stays as it was.
                if previous == "failed" and backtest.cells and all(
                    cell.status == "completed" for cell in backtest.cells
                ):
                    backtest.status, backtest.error_message = "completed", None
                else:
                    backtest.status = previous
        except Exception as exc:
            logger.exception("Settling backtest %s failed", backtest_id)
            with SessionLocal.begin() as session:
                backtest = session.get(Backtest, backtest_id)
                if backtest:
                    backtest.status = previous
                    backtest.error_message = f"Settling failed: {exc}"[:4000]
        finally:
            self._active.discard(backtest_id)

    def _next_cell(self, backtest_id: str, attempted: set[int]) -> tuple[int, str] | None:
        """The next cell this pass has not tried, with a fresh run to decide it."""
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            cell = next(
                (cell for cell in backtest.cells
                 if cell.status in RETRYABLE_CELLS and cell.id not in attempted),
                None,
            )
            if cell is None:
                return None
            run_id = self._prepare_run(session, backtest, cell)
            cell.run_id = run_id
            cell.status = "running"
            cell.error_message = None
            cell.started_at = _now()
            cell.completed_at = None
            return cell.id, run_id

    @staticmethod
    def _prepare_run(session, backtest: Backtest, cell: BacktestCell) -> str:
        """The cell's run, created or reset: a cell runs without checkpoints, start to finish."""
        settings = {key: value for key, value in backtest.config_snapshot.items() if key not in _GRID_FIELDS}
        config = {**settings, "ticker": cell.ticker, "trade_date": cell.trade_date.isoformat(),
                  "checkpoint_enabled": False, "use_portfolio": backtest.portfolio_snapshot is not None}
        estimate = round(backtest.estimated_cost_usd / max(1, len(backtest.cells)), 4)
        run = session.get(Run, cell.run_id) if cell.run_id else None
        if run is None:
            run = Run(
                id=str(uuid.uuid4()),
                ticker=cell.ticker,
                trade_date=cell.trade_date,
                asset_type=backtest.asset_type,
                selected_analysts=list(backtest.selected_analysts),
                llm_provider=backtest.llm_provider,
                deep_think_llm=backtest.deep_think_llm,
                quick_think_llm=backtest.quick_think_llm,
                temperature=settings.get("temperature"),
                max_debate_rounds=settings.get("max_debate_rounds", 1),
                max_risk_discuss_rounds=settings.get("max_risk_discuss_rounds", 1),
                checkpoint_enabled=False,
                output_language=settings.get("output_language", "English"),
                config_snapshot=config,
                estimated_cost_usd=estimate,
                portfolio_snapshot=backtest.portfolio_snapshot,
                backtest_id=backtest.id,
                status="queued",
            )
            session.add(run)
            session.add(Usage(run_id=run.id))
            session.flush()
            return run.id
        # A retried cell starts over: drop what its earlier attempt left.
        for model in (RunSection, RunEvent, Decision):
            session.execute(delete(model).where(model.run_id == run.id))
        usage = session.get(Usage, run.id)
        if usage is None:
            session.add(Usage(run_id=run.id))
        else:
            usage.llm_calls = usage.tool_calls = usage.tokens_in = usage.tokens_out = 0
            usage.estimated_cost_usd = 0
            usage.api_equivalent_usd = None
        run.status = "queued"
        run.error_message = None
        run.report_path = None
        run.started_at = run.completed_at = None
        return run.id

    def _finish_cell(self, cell_id: int, run_id: str) -> str | None:
        """Record how the cell's run ended; returns why the sweep must stop, if it must."""
        with SessionLocal.begin() as session:
            cell = session.get(BacktestCell, cell_id)
            run = session.get(Run, run_id)
            status = run.status if run else "failed"
            cell.error_message = None if status == "completed" else (run.error_message if run else "Run missing")
            blocker = sweep_blocker(cell.error_message) if status == "failed" else None
            # A cell stopped by a sweep-wide cause runs again on resume, like a stopped one.
            cell.status = "stopped" if blocker else status if status in {"completed", "stopped"} else "failed"
            cell.completed_at = _now()
            decision = session.get(Decision, run_id)
            if decision and decision.rating:
                cell.rating = decision.rating
            return blocker

    def _settle_sync(self, backtest_id: str) -> None:
        with SessionLocal() as session:
            backtest = session.get(Backtest, backtest_id)
            selected = list(backtest.selected_analysts)
            config = dict(backtest.config_snapshot)
            tickers = list(backtest.tickers)
            decided = any(cell.status == "completed" for cell in backtest.cells)
        if decided:
            results_dir, memory_log = backtest_paths(backtest_id)
            outcome = settle_backtest(selected_analysts=selected, config_overrides=config,
                                      results_dir=results_dir, memory_log_path=memory_log, tickers=tickers)
            with SessionLocal.begin() as session:
                backtest = session.get(Backtest, backtest_id)
                summary = dict(backtest.summary or {})
                previous = summary.get("settlement") or {}
                usage = dict(previous.get("usage") or {})
                for key, value in outcome["usage"].items():
                    # Counters add up; the per-tool data tallies ("data") describe
                    # a run's evidence, not a cost, and are not kept here.
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = round(usage.get(key, 0) + value, 6)
                summary["settlement"] = {
                    "settled": len(outcome["settled"]) + int(previous.get("settled", 0)),
                    "failed": [{"ticker": t, "date": d, "reason": r[:300]} for t, d, r in outcome["failed"]],
                    "usage": usage,
                }
                backtest.summary = summary
                backtest.settled_at = _now()
        self._refresh(backtest_id)

    def _refresh(self, backtest_id: str) -> None:
        """Read each cell's decision and outcome back from the sweep's memory log, and rescore."""
        memory_log = backtest_paths(backtest_id)[1]
        outcomes = backtest_outcomes(memory_log)
        scores = backtest_scores(memory_log)
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest is None:
                return
            for cell in backtest.cells:
                found = outcomes.get((cell.ticker, cell.trade_date.isoformat()))
                if not found:
                    continue
                cell.rating = found["rating"]
                cell.pending = found["pending"]
                cell.raw_return = found["raw_return"]
                cell.alpha_return = found["alpha_return"]
                cell.holding_days = found["holding_days"]
                cell.resolved_date = found["resolved_date"]
                cell.reflection = found["reflection"]
            summary = dict(backtest.summary or {})
            summary["upstream"] = scores
            summary["console"] = score_cells(backtest.cells)
            backtest.summary = summary

    def _set_status(self, backtest_id: str, status: str) -> None:
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest:
                backtest.status = status

    def _finish(self, backtest_id: str, status: str, message: str | None) -> None:
        with SessionLocal.begin() as session:
            backtest = session.get(Backtest, backtest_id)
            if backtest:
                backtest.status = status
                backtest.error_message = message
                backtest.completed_at = _now()

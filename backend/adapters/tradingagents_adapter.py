"""Version-sensitive adapter around the upstream TradingAgents package.

No module outside ``backend.adapters`` may import ``tradingagents``. Keeping
the semi-private streaming surface here gives upgrades one repair point.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, Field, ValidationError

from backend.app.settings import LLM_MAX_RETRIES, LLM_REQUEST_TIMEOUT_SECONDS, RESULTS_DIR
from tradingagents import __version__ as CORE_VERSION
from tradingagents.agents.rating import run_rating
from tradingagents.backtest import iter_grid, summarize as summarize_backtest
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors.yahoo.ohlcv import load_ohlcv
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph import trading_graph as _trading_graph
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients import (
    claude_code,
    create_tier_client as _create_tier_client,
    tier_provider,
)
from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from tradingagents.memory import TradingMemoryLog
from tradingagents.memory.settlement import resolve_benchmark
from tradingagents.output_languages import OUTPUT_LANGUAGE_OPTIONS
from tradingagents.portfolio import PortfolioContext, Position

logger = logging.getLogger(__name__)

# Providers billed to the person's own plan rather than per token.
PLAN_PROVIDERS = frozenset({"claude", "chatgpt"})


class RunStopped(Exception):
    """The run was stopped on request between two of its steps."""


def _console_tier_client(config: dict[str, Any], tier: str, **extra: Any):
    """Upstream's tier client, with the console's request timeout when one is set.

    v0.6 builds each tier's client inside ``TradingAgentsGraph.__init__`` and no
    longer exposes a provider-kwargs hook, so the console's ``llm_timeout`` is
    applied here. Graphs built without that key are unaffected.
    """
    timeout = config.get("llm_timeout")
    if timeout is not None:
        # A Claude plan call is one Claude Code process, so its timeout covers the
        # whole answer, not one read of a stream: never cut it below upstream's.
        if tier_provider(config, tier) == claude_code.PROVIDER:
            timeout = max(float(timeout), claude_code.DEFAULT_TIMEOUT)
        extra.setdefault("timeout", float(timeout))
    return _create_tier_client(config, tier, **extra)


_trading_graph.create_tier_client = _console_tier_client


_REASONING_CONTROLS: dict[str, dict[str, Any]] = {
    "openai": {
        "config_key": "openai_reasoning_effort",
        "env_var": "TRADINGAGENTS_OPENAI_REASONING_EFFORT",
        "label": "Reasoning effort",
        "levels": ["none", "low", "medium", "high", "xhigh", "max"],
        "default": "medium",
    },
    "google": {
        "config_key": "google_thinking_level",
        "env_var": "TRADINGAGENTS_GOOGLE_THINKING_LEVEL",
        "label": "Thinking level",
        "levels": ["minimal", "low", "medium", "high"],
        "default": "medium",
    },
    "anthropic": {
        "config_key": "anthropic_effort",
        "env_var": "TRADINGAGENTS_ANTHROPIC_EFFORT",
        "label": "Reasoning effort",
        "levels": ["low", "medium", "high"],
        "default": "high",
    },
    # The Claude plan takes the same setting through Claude Code's --effort; on a
    # plan, effort spends usage limits rather than money, so medium is suggested.
    "claude": {
        "config_key": "anthropic_effort",
        "env_var": "TRADINGAGENTS_ANTHROPIC_EFFORT",
        "label": "Reasoning effort",
        "levels": ["low", "medium", "high", "xhigh", "max"],
        "default": "medium",
    },
    "chatgpt": {
        "config_key": "openai_reasoning_effort",
        "env_var": "TRADINGAGENTS_OPENAI_REASONING_EFFORT",
        "label": "Reasoning effort",
        "levels": ["low", "medium", "high"],
        "default": "medium",
    },
}


def _reasoning_control(provider: str, model: str) -> dict[str, Any] | None:
    """Describe the provider knobs that upstream forwards to its LLM clients.

    Upstream v0.5.0 no longer publishes UI metadata in ``model_catalog`` even
    though the three configuration keys remain supported. Keep this web-only
    presentation metadata at the adapter boundary instead of restoring it to
    the core package.
    """
    control = _REASONING_CONTROLS.get(provider.lower())
    if control is None:
        return None
    result = copy.deepcopy(control)
    model_name = model.lower()
    if provider == "google" and model_name != "custom" and "flash-lite" in model_name:
        result["default"] = "minimal"
    return result


class ConsoleTradingAgentsGraph(TradingAgentsGraph):
    """The upstream graph as the console runs it; see ``_console_tier_client``."""


@dataclass(frozen=True)
class SectionEvent:
    section_key: str
    content_md: str
    structured_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class AdapterResult:
    final_state: dict[str, Any]
    decision: str | None
    usage: dict[str, Any]
    report_path: Path
    degraded_streaming: bool = False


@dataclass(frozen=True)
class MarketChartData:
    symbol: str
    as_of: str
    candles: list[dict[str, str | float]]


def core_version() -> str:
    """The version of the core package this console runs.

    Read from the code, not the install's metadata: an editable install keeps the
    version it was installed at until it is reinstalled, so after an upstream
    merge the metadata named the release before it.
    """
    return CORE_VERSION or "unknown"


def load_current_market_chart(symbol: str, limit: int = 180, since: date | None = None) -> MarketChartData:
    """Load current daily OHLC candles through the upstream market-data path.

    ``since`` keeps that date on the chart, with a few weeks before it, however
    long ago it was: what the price did after an analysis is how it is judged.
    """
    canonical = normalize_symbol(symbol)
    frame = load_ohlcv(symbol, date.today().isoformat())
    start = len(frame) - limit
    if since is not None:
        before = int((frame["Date"] < pd.Timestamp(since)).sum())
        start = min(start, before - 40)
    frame = frame.iloc[max(0, start):]
    candles = [
        {
            "time": row.Date.strftime("%Y-%m-%d"),
            "open": float(row.Open),
            "high": float(row.High),
            "low": float(row.Low),
            "close": float(row.Close),
        }
        for row in frame.itertuples()
    ]
    if not candles:
        raise RuntimeError(f"No current market candles are available for {symbol}")
    return MarketChartData(symbol=canonical, as_of=str(candles[-1]["time"]), candles=candles)


DATA_OUTCOMES = ("ok", "empty", "withheld", "unavailable", "error")


def data_outcome(output: Any) -> str:
    """What a data tool's answer was: data, none found, withheld, unavailable, or an error.

    Upstream answers in text: a vendor that failed returns a ``DATA_UNAVAILABLE``
    or ``<... unavailable ...>`` placeholder, a past run is told live-only data is
    "withheld for" its date, and a tool error comes back as "Error: ...".
    """
    text = str(getattr(output, "content", output) or "").lstrip()
    head = text[:400]
    if not text:
        return "empty"
    if head.startswith(("DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")) or (
        head.startswith("<") and " unavailable " in head
    ):
        return "unavailable"
    if "withheld for" in head:
        return "withheld"
    if head.startswith(("Error", "start_date", "Indicator ")) or "is not supported" in head:
        return "error"
    if head.startswith(("No headlines", "No news found", "No market headlines", "No global news")):
        return "empty"
    return "ok"


class StatsCallbackHandler(BaseCallbackHandler):
    """Thread-safe equivalent of the upstream CLI stats callback.

    It also tallies what each data tool returned, so a run that decided without
    its news or fundamentals says so instead of reading like a full analysis.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        # Reported by plan-billed calls (Claude Code): the same tokens at API prices.
        self.api_equivalent_usd = 0.0
        self._open_tools: dict[Any, str] = {}
        self.data: dict[str, dict[str, int]] = {}

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        with self._lock:
            self.llm_calls += 1

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return
        message = getattr(generation, "message", None)
        usage = getattr(message, "usage_metadata", None) if isinstance(message, AIMessage) else None
        metadata = getattr(message, "response_metadata", None) or {}
        notional = metadata.get("api_equivalent_cost_usd")
        with self._lock:
            if usage:
                self.tokens_in += int(usage.get("input_tokens", 0))
                self.tokens_out += int(usage.get("output_tokens", 0))
            if isinstance(notional, (int, float)):
                self.api_equivalent_usd += float(notional)

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        with self._lock:
            self.tool_calls += 1
            self._open_tools[kwargs.get("run_id")] = name

    def on_tool_end(self, output, **kwargs) -> None:
        self._tally(kwargs.get("run_id"), data_outcome(output))

    def on_tool_error(self, error, **kwargs) -> None:
        self._tally(kwargs.get("run_id"), "error")

    def _tally(self, run_id: Any, outcome: str) -> None:
        with self._lock:
            name = self._open_tools.pop(run_id, None)
            if name is not None:
                counts = self.data.setdefault(name, dict.fromkeys(DATA_OUTCOMES, 0))
                counts[outcome] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "api_equivalent_usd": round(self.api_equivalent_usd, 6),
                "data": {name: dict(counts) for name, counts in self.data.items()},
            }


def upstream_catalog() -> dict[str, Any]:
    """Return upstream provider/model metadata without leaking key values."""
    providers = {}
    google_flash_lite = None
    custom_only = {
        "quick": [("Custom model ID", "custom")],
        "deep": [("Custom model ID", "custom")],
    }
    for provider in sorted(set(MODEL_OPTIONS) | set(PROVIDER_API_KEY_ENV)):
        modes = MODEL_OPTIONS.get(provider, custom_only)
        quick_options = list(modes["quick"])
        deep_options = list(modes["deep"])
        # Gemini Flash Lite is a valid upstream-known model and is useful as a
        # low-cost synthesis default even though the CLI catalogs it under the
        # quick-model column only.
        if provider == "google":
            flash_lite = next(
                (option for option in quick_options if "flash-lite" in option[1]), None
            )
            if flash_lite and flash_lite not in deep_options:
                deep_options.insert(0, flash_lite)
            google_flash_lite = flash_lite[1] if flash_lite else quick_options[0][1]
        def model_option(option: tuple[str, str], provider_name: str = provider) -> dict[str, Any]:
            label, value = option
            reasoning = _reasoning_control(provider_name, value)
            return {
                "label": label,
                "value": value,
                "reasoning_levels": reasoning["levels"] if reasoning else [],
                "default_reasoning_level": reasoning["default"] if reasoning else None,
            }

        provider_reasoning = _reasoning_control(provider, "custom")
        providers[provider] = {
            "key_env": PROVIDER_API_KEY_ENV.get(provider),
            "quick": [model_option(option) for option in quick_options],
            "deep": [model_option(option) for option in deep_options],
            "reasoning": provider_reasoning,
        }
    return {
        "providers": providers,
        "output_languages": [
            {"label": label, "value": value}
            for label, value in OUTPUT_LANGUAGE_OPTIONS
        ],
        "defaults": {
            "llm_provider": "google",
            "deep_think_llm": google_flash_lite,
            "quick_think_llm": google_flash_lite,
            "temperature": DEFAULT_CONFIG.get("temperature"),
            "max_debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
            "max_risk_discuss_rounds": DEFAULT_CONFIG["max_risk_discuss_rounds"],
            "checkpoint_enabled": True,
            "output_language": DEFAULT_CONFIG["output_language"],
            "google_thinking_level": DEFAULT_CONFIG.get("google_thinking_level"),
            "openai_reasoning_effort": DEFAULT_CONFIG.get("openai_reasoning_effort"),
            "anthropic_effort": DEFAULT_CONFIG.get("anthropic_effort"),
        },
        "vendors": copy.deepcopy(DEFAULT_CONFIG.get("data_vendors", {})),
    }


def _jsonable(value: Any) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            return None
        return value
    return None


# UI section -> (state container, key), in pipeline order. Since v0.5.2 each
# decision has one name in the state: the Research Manager's is
# ``investment_plan`` and the Portfolio Manager's ``final_trade_decision``; the
# debate states no longer carry a ``judge_decision`` copy.
_SECTION_SOURCES: tuple[tuple[str, str | None, str], ...] = (
    ("market_report", None, "market_report"),
    ("sentiment_report", None, "sentiment_report"),
    ("news_report", None, "news_report"),
    ("fundamentals_report", None, "fundamentals_report"),
    ("bull", "investment_debate_state", "bull_history"),
    ("bear", "investment_debate_state", "bear_history"),
    ("research_manager", None, "investment_plan"),
    ("trader", None, "trader_investment_plan"),
    ("risk_aggressive", "risk_debate_state", "aggressive_history"),
    ("risk_conservative", "risk_debate_state", "conservative_history"),
    ("risk_neutral", "risk_debate_state", "neutral_history"),
    ("portfolio_manager", None, "final_trade_decision"),
)


def _extract_section_events(state: dict[str, Any]) -> list[SectionEvent]:
    """Map accumulated LangGraph state to UI sections.

    Upstream renders typed decisions back to markdown before placing them in
    state. We intentionally do not regex those documents. If an upstream
    release exposes its Pydantic value alongside the rendered text, this helper
    will persist it through the conventional ``*_structured`` keys.
    """
    events: list[SectionEvent] = []
    for section_key, container, key in _SECTION_SOURCES:
        source = (state.get(container) or {}) if container else state
        content = source.get(key)
        if content:
            structured = _jsonable(content) or _jsonable(source.get(f"{key}_structured"))
            events.append(SectionEvent(section_key, str(content), structured))
    return events


def _build_config(
    config_overrides: dict[str, Any],
    results_dir: Path,
    memory_log_path: Path | None = None,
) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    allowed = {
        "llm_provider",
        "deep_think_llm",
        "quick_think_llm",
        "temperature",
        "max_debate_rounds",
        "max_risk_discuss_rounds",
        "checkpoint_enabled",
        "output_language",
        "benchmark_ticker",
        "data_vendors",
        "google_thinking_level",
        "openai_reasoning_effort",
        "anthropic_effort",
    }
    # Optional request fields arrive as None when omitted. Never replace a
    # populated upstream default (notably data_vendors) with that sentinel.
    config.update(
        {
            key: value
            for key, value in config_overrides.items()
            if key in allowed and value is not None
        }
    )
    # Upstream writes each run's JSON state log under results_dir; the report
    # tree itself goes to the run's own report directory.
    config["results_dir"] = str(results_dir)
    if memory_log_path is not None:
        config["memory_log_path"] = str(memory_log_path)
    config["llm_timeout"] = LLM_REQUEST_TIMEOUT_SECONDS
    config["llm_max_retries"] = LLM_MAX_RETRIES
    return config


# --- the caller's book --------------------------------------------------------------


class ConsolePosition(Position):
    currency: str | None = Field(default=None, description="Currency of the average price, e.g. USD or AUD")


class ConsolePortfolio(PortfolioContext):
    """Upstream's book, with the currency of each position's average price.

    A mixed book (US shares bought in USD, ASX shares in AUD, cash in AUD) would
    otherwise reach the agents as bare prices beside one currency label. Upstream
    only calls ``render`` and ``fingerprint`` on the book, so this subclass changes
    what the agents read without touching upstream; a book without position
    currencies renders and fingerprints exactly as upstream's does.
    """

    positions: list[ConsolePosition] = Field(default_factory=list)

    def render(self, ticker: str) -> str:
        lines = super().render(ticker).split("\n")
        # Upstream writes one "Current position" line per holding of the instrument
        # (several when a broker lists it under more than one spelling), in this order.
        rows = [i for i, line in enumerate(lines) if line.startswith("- Current position in ")]
        notes: list[str] = []
        for row, held in zip(rows, self.positions_in(ticker), strict=False):
            if not held.currency or held.average_price is None:
                continue
            price = f", average price {held.average_price:,.2f}"
            lines[row] = lines[row].replace(price, f"{price} {held.currency}", 1)
            note = f"- The average price is in {held.currency}; cash is in {self.currency}."
            if self.currency and held.currency != self.currency and note not in notes:
                notes.append(note)
        return "\n".join(lines + notes)

    def fingerprint(self) -> str:
        if any(p.currency for p in self.positions):
            return super().fingerprint()
        # Unchanged for a book saved before positions had a currency, so its runs resume.
        return PortfolioContext.model_validate(self.model_dump(exclude={"positions": {"__all__": {"currency"}}})).fingerprint()


def normalize_portfolio(data: dict[str, Any]) -> dict[str, Any]:
    """The book as upstream reads it; raises ValueError naming what is wrong."""
    try:
        return ConsolePortfolio.model_validate(data).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError(str(exc)) from None


def _portfolio(snapshot: dict[str, Any] | None) -> PortfolioContext | None:
    # None is "no portfolio context", which upstream keeps distinct from a flat
    # book (a snapshot whose positions list is empty).
    return None if snapshot is None else ConsolePortfolio.model_validate(snapshot)


def portfolio_context(snapshot: dict[str, Any] | None, ticker: str) -> str | None:
    """The portfolio block the trader, risk analysts and portfolio manager read."""
    book = _portfolio(snapshot)
    return book.render(ticker) if book is not None else None


# --- one analysis -------------------------------------------------------------------


def run_streaming_analysis(
    *,
    run_id: str,
    ticker: str,
    trade_date: str,
    asset_type: str,
    selected_analysts: list[str],
    config_overrides: dict[str, Any],
    report_dir: Path,
    on_section: Callable[[SectionEvent, dict[str, Any]], None],
    on_status: Callable[[str, dict[str, Any]], None],
    portfolio: dict[str, Any] | None = None,
    results_dir: Path | None = None,
    memory_log_path: Path | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AdapterResult:
    """Execute TradingAgents with accumulated-state streaming and safe fallback.

    ``portfolio`` is the saved book the decision agents work against (None for
    none). A backtest cell passes its sweep's ``results_dir`` and
    ``memory_log_path``. ``should_stop`` is polled between graph steps; when it
    returns True the run raises ``RunStopped``, keeping any checkpoint.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    config = _build_config(config_overrides, results_dir or RESULTS_DIR, memory_log_path)
    book = _portfolio(portfolio)
    stats = StatsCallbackHandler()
    seen: dict[str, tuple[str, dict[str, Any] | None]] = {}
    graph: ConsoleTradingAgentsGraph | None = None
    checkpoint_started = False

    def emit_changed(state: dict[str, Any]) -> None:
        for event in _extract_section_events(state):
            signature = (event.content_md, event.structured_json)
            if seen.get(event.section_key) != signature:
                seen[event.section_key] = signature
                on_section(event, stats.snapshot())

    def check_stop() -> None:
        if should_stop is not None and should_stop():
            raise RunStopped()

    try:
        check_stop()
        graph = ConsoleTradingAgentsGraph(
            selected_analysts=selected_analysts,
            debug=False,
            config=config,
            callbacks=[stats],
        )
        init_state = graph.create_run_state(ticker, trade_date, asset_type, book)
        args = graph.propagator.get_graph_args(callbacks=[stats])
        checkpoint_tid = graph.begin_checkpoint(ticker, trade_date, asset_type, book)
        checkpoint_started = True
        if checkpoint_tid is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_tid

        # stream_run yields whole top-level states and, as each parallel analyst
        # files its report, that analyst's report alone. Merge them as the CLI
        # does so a report appears the moment its analyst finishes.
        final_state: dict[str, Any] = {}
        for _messages, chunk in graph.stream_run(graph.checkpoint_input(init_state), **args):
            check_stop()
            if chunk is None:
                continue
            final_state.update(chunk)
            emit_changed(final_state)
        if not final_state.get("final_trade_decision"):
            raise RuntimeError("TradingAgents completed without returning a final decision")

        # record_decision writes the state log as well as the memory log entry.
        graph.record_decision(ticker, trade_date, final_state)
        graph.clear_checkpoint_on_success(ticker, trade_date, asset_type, book)
        decision = run_rating(final_state)
        report_path = graph.save_reports(final_state, ticker, save_path=report_dir)
        return AdapterResult(final_state, decision, stats.snapshot(), report_path)
    except Exception as streaming_error:
        # Once real graph output exists, this is an execution/provider failure,
        # not a streaming-interface incompatibility. Re-running from scratch
        # would duplicate calls and can hang a second time; let the orchestrator
        # surface a resumable failure instead.
        compatibility_error = isinstance(
            streaming_error, (AttributeError, NotImplementedError, TypeError)
        )
        if seen or not compatibility_error:
            raise
        logger.exception("Streaming adapter failed for run %s; falling back to propagate", run_id)
        on_status(
            "streaming_degraded",
            {
                "message": "Granular progress is temporarily unavailable; the run continues in compatibility mode.",
                "adapter_error": type(streaming_error).__name__,
            },
        )
        if graph is not None and checkpoint_started:
            graph.end_checkpoint()
            checkpoint_started = False
        fallback = ConsoleTradingAgentsGraph(
            selected_analysts=selected_analysts,
            debug=False,
            config=config,
            callbacks=[stats],
        )
        final_state, decision = fallback.propagate(
            ticker, trade_date, asset_type=asset_type, portfolio=book
        )
        emit_changed(final_state)
        report_path = fallback.save_reports(final_state, ticker, save_path=report_dir)
        return AdapterResult(final_state, decision, stats.snapshot(), report_path, degraded_streaming=True)
    finally:
        if graph is not None and checkpoint_started:
            graph.end_checkpoint()


# --- plan-billed providers ----------------------------------------------------------


def _masked_account(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    name, _, domain = email.partition("@")
    return f"{name[:1]}•••@{domain}"


def claude_plan_status(fresh: bool = False) -> dict[str, Any]:
    """Whether Claude Code on this machine can run analyses on a Claude plan.

    The console never sees the plan credential: it asks Claude Code. A plan
    token in ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude setup-token``, for a
    container) counts as signed in, as it does for the provider itself.
    """
    login = "tradingagents login claude"
    try:
        cli = claude_code.find_cli()
    except claude_code.ClaudeCodeError as exc:
        return {"ready": False, "installed": False, "message": str(exc), "login_command": login}
    version = claude_code.cli_version(cli)
    base = {
        "installed": True,
        "cli_path": cli,
        "version": ".".join(str(part) for part in version) if version else None,
        "login_command": login,
        "token_env": claude_code.TOKEN_ENV,
    }
    try:
        claude_code.ensure_supported(cli)
    except claude_code.ClaudeCodeError as exc:
        return {**base, "ready": False, "message": str(exc)}
    token = bool(os.environ.get(claude_code.TOKEN_ENV))
    status = claude_code.auth_status(cli, fresh=fresh)
    signed_in = claude_code.on_plan(status)
    if signed_in:
        message = f"Signed in to a Claude {str(status.get('subscriptionType') or '').title()} plan".replace("  ", " ")
    elif token:
        message = f"Using the plan token in {claude_code.TOKEN_ENV}"
    elif status.get("loggedIn"):
        message = "Claude Code is signed in to Anthropic API billing, not a Claude plan."
    else:
        message = "Claude Code is not signed in to a Claude plan."
    return {
        **base,
        "ready": signed_in or token,
        "signed_in": signed_in,
        "token": token,
        "account": _masked_account(status.get("email")),
        "plan": status.get("subscriptionType"),
        "message": message,
    }


class PlanSignInError(RuntimeError):
    """A sign-in to a plan that could not be started or finished; the message says why."""


def claude_sign_in_start() -> dict[str, Any]:
    """Start Claude Code's own login here and return the claude.com link to approve it at.

    For a backend without a browser (a container): the person opens the link in
    theirs and pastes back the code claude.com shows. Claude Code keeps the
    sign-in in its config directory and refreshes it; the console relays the
    one-time code only.
    """
    try:
        url = claude_code.start_browser_sign_in()
    except claude_code.ClaudeCodeError as exc:
        raise PlanSignInError(str(exc)) from None
    return {"url": url, "expires_in": int(claude_code.LOGIN_WINDOW)}


def claude_sign_in_finish(code: str) -> dict[str, Any]:
    """Pass the code claude.com showed to the waiting login; returns the plan status."""
    try:
        claude_code.finish_browser_sign_in(code)
    except claude_code.ClaudeCodeError as exc:
        raise PlanSignInError(str(exc)) from None
    return claude_plan_status(fresh=True)


def claude_sign_in_cancel() -> None:
    claude_code.cancel_browser_sign_in()


def chatgpt_plan_status() -> dict[str, Any]:
    """Whether this machine is signed in to a ChatGPT plan (``tradingagents login chatgpt``)."""
    from tradingagents.llm_clients import chatgpt_auth

    email = chatgpt_auth.signed_in_as()
    return {
        "ready": email is not None,
        "installed": True,
        "account": _masked_account(email),
        "login_command": "tradingagents login chatgpt",
        "message": "Signed in to a ChatGPT plan" if email else "Not signed in to a ChatGPT plan.",
    }


# --- backtests ----------------------------------------------------------------------


def backtest_dates(start_date: str, end_date: str, every_n_days: int) -> list[str]:
    """The analysis dates of a grid, exactly as upstream's backtest builds them."""
    return iter_grid(start_date, end_date, every_n_days)


def backtest_benchmark(ticker: str, config_overrides: dict[str, Any]) -> str:
    """The index a ticker's alpha is measured against."""
    return resolve_benchmark(ticker, _build_config(config_overrides, RESULTS_DIR))


def _percent(text: str | None) -> float | None:
    """A memory-log percentage ("+1.2%") as a fraction, or None when there is none."""
    value = (text or "").strip().rstrip("%")
    try:
        return float(value) / 100
    except ValueError:
        return None


def _days(text: str | None) -> int | None:
    value = (text or "").strip().rstrip("d")
    return int(value) if value.isdigit() else None


def backtest_outcomes(memory_log_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Each logged decision of a sweep, keyed by (ticker, date): its rating and, once settled, outcome."""
    if not memory_log_path.is_file():
        return {}
    outcomes = {}
    for entry in TradingMemoryLog({"memory_log_path": str(memory_log_path)}).load_entries():
        outcomes[(entry["ticker"], entry["date"])] = {
            "rating": entry["rating"],
            "pending": bool(entry["pending"]),
            "raw_return": _percent(entry.get("raw")),
            "alpha_return": _percent(entry.get("alpha")),
            "holding_days": _days(entry.get("holding")),
            "resolved_date": entry.get("resolved"),
            "reflection": entry.get("reflection") or None,
        }
    return outcomes


def backtest_scores(memory_log_path: Path) -> dict[str, Any] | None:
    """Upstream's score of a sweep's settled decisions, grouped by rating."""
    if not memory_log_path.is_file():
        return None
    summary = summarize_backtest(memory_log_path)
    return {
        "resolved": summary.resolved,
        "pending": summary.pending,
        "unscored": summary.unscored,
        "holding": summary.holding,
        "by_rating": {
            rating: {"count": score.count, "hit_rate": score.hit_rate, "mean_alpha": score.mean_alpha}
            for rating, score in summary.by_rating.items()
        },
        "text": summary.render(),
    }


def settle_backtest(
    *,
    selected_analysts: list[str],
    config_overrides: dict[str, Any],
    results_dir: Path,
    memory_log_path: Path,
    tickers: list[str],
) -> dict[str, Any]:
    """Settle every decision of a sweep whose holding window has traded.

    A run settles the log's due decisions while its analysts work, so a
    ticker's last cell stays pending until this pass, as in upstream's
    ``run_backtest``. Each settlement asks the quick model for a reflection.
    """
    config = _build_config(config_overrides, results_dir, memory_log_path)
    stats = StatsCallbackHandler()
    graph = ConsoleTradingAgentsGraph(
        selected_analysts=selected_analysts, debug=False, config=config, callbacks=[stats]
    )
    settled: list[tuple[str, str]] = []
    failed: list[tuple[str, str, str]] = []
    for ticker in tickers:
        try:
            done = graph.settle_pending(ticker)
        except Exception as exc:  # reflection calls a provider; one ticker's failure is not the sweep's
            logger.warning("Settling %s failed: %s", ticker, exc)
            failed.append((ticker, "", str(exc)))
            continue
        settled.extend(done.settled)
        failed.extend(done.failed)
    return {"settled": settled, "failed": failed, "usage": stats.snapshot()}


# What each rating claims will happen, as upstream's backtest scores it: Hold
# claims no direction, so no outcome proves it right or wrong.
RATING_DIRECTION = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}


def sweep_blocker(error_message: str | None) -> str | None:
    """Why every remaining backtest cell would fail the same way, or None.

    A reached plan limit, a refused sign-in or a missing Claude Code is not about
    one cell: the sweep stops instead of failing each cell in turn, and resumes
    once the cause is fixed.
    """
    text = error_message or ""
    if "usage limit is reached" in text:
        return "Your Claude plan's usage limit is reached. Resume this backtest once it resets."
    if claude_code.LOGIN_HINT in text:
        return "Claude Code is not signed in to a Claude plan. Run `tradingagents login claude`, then resume."
    if claude_code.INSTALL_HINT in text:
        return "Claude Code was not found on the backend's machine. Install it, then resume."
    return None

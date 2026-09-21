"""Version-sensitive adapter around the upstream TradingAgents package.

No module outside ``backend.adapters`` may import ``tradingagents``. Keeping
the semi-private streaming surface here gives upgrades one repair point.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult
from pydantic import BaseModel

from backend.app.settings import LLM_MAX_RETRIES, LLM_REQUEST_TIMEOUT_SECONDS
from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS
from tradingagents.output_languages import OUTPUT_LANGUAGE_OPTIONS

logger = logging.getLogger(__name__)


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
    """Apply web-console reliability limits without changing upstream code."""

    def _get_provider_kwargs(self) -> dict[str, Any]:
        kwargs = super()._get_provider_kwargs()
        timeout = self.config.get("llm_timeout")
        if timeout is not None:
            kwargs["timeout"] = float(timeout)
        return kwargs


@dataclass(frozen=True)
class SectionEvent:
    section_key: str
    content_md: str
    structured_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class AdapterResult:
    final_state: dict[str, Any]
    decision: str | None
    usage: dict[str, int]
    report_path: Path
    degraded_streaming: bool = False


@dataclass(frozen=True)
class MarketChartData:
    symbol: str
    as_of: str
    candles: list[dict[str, str | float]]


def load_current_market_chart(symbol: str, limit: int = 180) -> MarketChartData:
    """Load current daily OHLC candles through the upstream market-data path."""
    canonical = normalize_symbol(symbol)
    frame = load_ohlcv(symbol, date.today().isoformat()).tail(limit)
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


class StatsCallbackHandler(BaseCallbackHandler):
    """Thread-safe equivalent of the upstream CLI stats callback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

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
        if usage:
            with self._lock:
                self.tokens_in += int(usage.get("input_tokens", 0))
                self.tokens_out += int(usage.get("output_tokens", 0))

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        with self._lock:
            self.tool_calls += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
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


def _extract_section_events(state: dict[str, Any]) -> list[SectionEvent]:
    """Map accumulated LangGraph state to UI sections.

    Current v0.5.0 renders typed decisions back to markdown before placing them
    in state. We intentionally do not regex those documents. If an upstream
    release exposes its Pydantic value alongside the rendered text, this helper
    will persist it through the conventional ``*_structured`` keys.
    """
    events: list[SectionEvent] = []
    direct = {
        "market_report": "market_report",
        "sentiment_report": "sentiment_report",
        "news_report": "news_report",
        "fundamentals_report": "fundamentals_report",
        "trader": "trader_investment_plan",
    }
    for section_key, state_key in direct.items():
        content = state.get(state_key)
        if content:
            structured = _jsonable(content) or _jsonable(state.get(f"{state_key}_structured"))
            events.append(SectionEvent(section_key, str(content), structured))

    debate = state.get("investment_debate_state") or {}
    for section_key, key in (
        ("bull", "bull_history"),
        ("bear", "bear_history"),
        ("research_manager", "judge_decision"),
    ):
        if debate.get(key):
            structured = _jsonable(debate[key]) or _jsonable(debate.get(f"{key}_structured"))
            events.append(SectionEvent(section_key, str(debate[key]), structured))

    risk = state.get("risk_debate_state") or {}
    for section_key, key in (
        ("risk_aggressive", "aggressive_history"),
        ("risk_conservative", "conservative_history"),
        ("risk_neutral", "neutral_history"),
        ("portfolio_manager", "judge_decision"),
    ):
        if risk.get(key):
            structured = _jsonable(risk[key]) or _jsonable(risk.get(f"{key}_structured"))
            events.append(SectionEvent(section_key, str(risk[key]), structured))
    return events


def _build_config(config_overrides: dict[str, Any], results_dir: Path) -> dict[str, Any]:
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
    config["results_dir"] = str(results_dir.parent)
    config["llm_timeout"] = LLM_REQUEST_TIMEOUT_SECONDS
    config["llm_max_retries"] = LLM_MAX_RETRIES
    return config


def run_streaming_analysis(
    *,
    run_id: str,
    ticker: str,
    trade_date: str,
    asset_type: str,
    selected_analysts: list[str],
    config_overrides: dict[str, Any],
    report_dir: Path,
    on_section: Callable[[SectionEvent, dict[str, int]], None],
    on_status: Callable[[str, dict[str, Any]], None],
) -> AdapterResult:
    """Execute TradingAgents with accumulated-state streaming and safe fallback."""
    report_dir.mkdir(parents=True, exist_ok=True)
    config = _build_config(config_overrides, report_dir)
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

    try:
        graph = ConsoleTradingAgentsGraph(
            selected_analysts=selected_analysts,
            debug=False,
            config=config,
            callbacks=[stats],
        )
        graph.ticker = ticker
        init_state = graph.create_run_state(ticker, trade_date, asset_type)
        args = graph.propagator.get_graph_args(callbacks=[stats])
        checkpoint_tid = graph.begin_checkpoint(ticker, trade_date, asset_type)
        checkpoint_started = True
        if checkpoint_tid is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_tid

        final_state: dict[str, Any] | None = None
        for chunk in graph.graph.stream(graph.checkpoint_input(init_state), **args):
            final_state = chunk
            emit_changed(chunk)
        if final_state is None:
            raise RuntimeError("TradingAgents completed without returning state")

        graph.curr_state = final_state
        graph._log_state(trade_date, final_state)
        graph.record_decision(ticker, trade_date, final_state)
        graph.clear_checkpoint_on_success(ticker, trade_date, asset_type)
        decision = graph.process_signal(final_state["final_trade_decision"])
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
        final_state, decision = fallback.propagate(ticker, trade_date, asset_type=asset_type)
        emit_changed(final_state)
        report_path = fallback.save_reports(final_state, ticker, save_path=report_dir)
        return AdapterResult(final_state, decision, stats.snapshot(), report_path, degraded_streaming=True)
    finally:
        if graph is not None and checkpoint_started:
            graph.end_checkpoint()

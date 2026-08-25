from __future__ import annotations

import json
import os

# Editable without a deploy through TRADING_CONSOLE_MODEL_COSTS_JSON. Values are
# rough USD per 1K tokens, not provider billing guarantees.
DEFAULT_MODEL_COSTS: dict[str, dict[str, float]] = {
    "default": {"input": 0.002, "output": 0.008},
    "local": {"input": 0.0, "output": 0.0},
    "gpt-5.4-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-5.4-nano": {"input": 0.0001, "output": 0.0004},
    "claude-haiku-4-5": {"input": 0.001, "output": 0.005},
}


def price_table() -> dict[str, dict[str, float]]:
    raw = os.getenv("TRADING_CONSOLE_MODEL_COSTS_JSON")
    if not raw:
        return DEFAULT_MODEL_COSTS
    loaded = json.loads(raw)
    return {**DEFAULT_MODEL_COSTS, **loaded}


def model_rate(provider: str, model: str) -> dict[str, float]:
    if provider in {"ollama", "openai_compatible"} and provider == "ollama":
        return price_table()["local"]
    return price_table().get(model, price_table()["default"])


def estimate_run_cost(
    *,
    provider: str,
    quick_model: str,
    deep_model: str,
    analysts: int,
    debate_rounds: int,
    risk_rounds: int,
) -> float:
    quick_calls = analysts * 2 + 1
    deep_calls = debate_rounds * 2 + risk_rounds * 3 + 2
    # Assumption: tool-heavy analyst calls average 5K input/1.5K output; synthesis
    # calls average 8K input/2K output. The UI labels this a rough guardrail.
    quick = model_rate(provider, quick_model)
    deep = model_rate(provider, deep_model)
    estimate = quick_calls * (5 * quick["input"] + 1.5 * quick["output"])
    estimate += deep_calls * (8 * deep["input"] + 2 * deep["output"])
    return round(estimate, 4)


def actual_usage_cost(
    *, provider: str, quick_model: str, deep_model: str, tokens_in: int, tokens_out: int
) -> float:
    # Callback metadata cannot reliably attribute each token to quick vs deep;
    # use the midpoint rate and keep the value explicitly labeled estimated.
    quick = model_rate(provider, quick_model)
    deep = model_rate(provider, deep_model)
    input_rate = (quick["input"] + deep["input"]) / 2
    output_rate = (quick["output"] + deep["output"]) / 2
    return round(tokens_in / 1000 * input_rate + tokens_out / 1000 * output_rate, 4)


def public_cost_table() -> dict[str, object]:
    return {
        "currency": "USD",
        "unit": "per_1k_tokens",
        "prices": price_table(),
        "assumptions": {
            "quick_call_tokens": {"input": 5000, "output": 1500},
            "deep_call_tokens": {"input": 8000, "output": 2000},
            "notice": "A rough planning estimate; provider billing and tool use can differ.",
        },
    }

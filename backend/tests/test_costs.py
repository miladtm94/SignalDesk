from backend.app.services.costs import actual_usage_cost, estimate_run_cost


def test_cost_estimate_increases_with_debate_rounds():
    baseline = estimate_run_cost(
        provider="openai",
        quick_model="unknown-quick",
        deep_model="unknown-deep",
        analysts=4,
        debate_rounds=1,
        risk_rounds=1,
    )
    expanded = estimate_run_cost(
        provider="openai",
        quick_model="unknown-quick",
        deep_model="unknown-deep",
        analysts=4,
        debate_rounds=3,
        risk_rounds=2,
    )
    assert expanded > baseline > 0


def test_ollama_usage_is_free_in_cost_guardrail():
    assert actual_usage_cost(
        provider="ollama",
        quick_model="qwen3:latest",
        deep_model="qwen3:latest",
        tokens_in=100_000,
        tokens_out=20_000,
    ) == 0

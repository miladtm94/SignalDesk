"""Shared model catalog for CLI selections and validation."""

from __future__ import annotations

ModelOption = tuple[str, str]
ProviderModeOptions = dict[str, dict[str, list[ModelOption]]]

# Providers that serve many / frequently-changing models: offer only "Custom
# model ID" rather than a list that goes stale.
_CUSTOM_ONLY: dict[str, list[ModelOption]] = {
    "quick": [("Custom model ID", "custom")],
    "deep": [("Custom model ID", "custom")],
}


# Shared model list for GLM via Z.AI (international) and BigModel (China).
# Source: docs.z.ai (GLM Coding Plan supported models + LLM guides).
# All GLM 4.7+ entries support thinking mode via thinking={"type":"enabled"}.
_GLM_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("GLM-5-Turbo - Fast, switchable thinking modes", "glm-5-turbo"),
        ("GLM-4.7 - Previous-gen flagship", "glm-4.7"),
        ("GLM-4.5-Air - Lightweight, cost-efficient", "glm-4.5-air"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("GLM-5.2 - Latest flagship, 1M ctx", "glm-5.2"),
        ("GLM-5.1 - 745B, 200K ctx", "glm-5.1"),
        ("GLM-5 - Flagship, 204K ctx", "glm-5"),
        ("GLM-4.7 - Previous-gen flagship", "glm-4.7"),
        ("Custom model ID", "custom"),
    ],
}


# Shared model list for Qwen's global (dashscope-intl) and CN (dashscope) endpoints.
# Source: modelstudio.console.alibabacloud.com (Featured Models — Flagship + Cost-optimized).
#
# Only versioned IDs are exposed in the dropdown. The version-less aliases
# (qwen-plus, qwen-flash) are documented by Alibaba as auto-upgrading
# pointers ("backbone, latest, and snapshot ... have been upgraded to the
# Qwen3 series"), which means their behavior shifts when Alibaba rotates
# the backing model. Users who want a specific generation pick it
# explicitly; users who really want auto-latest can enter the alias via
# "Custom model ID".
_QWEN_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("Qwen 3.7 Plus - Latest, balanced speed/cost", "qwen3.7-plus"),
        ("Qwen 3.6 Plus - Previous-gen balanced", "qwen3.6-plus"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("Qwen 3.7 Max - Latest flagship, most intelligent, 1M ctx", "qwen3.7-max"),
        ("Qwen 3.6 Max - Previous-gen flagship", "qwen3.6-max"),
        ("Qwen 3.7 Plus - Balanced alternative", "qwen3.7-plus"),
        ("Custom model ID", "custom"),
    ],
}


# Shared model list for MiniMax's global and CN endpoints (same IDs).
# Full official lineup per platform.minimax.io/docs/api-reference/text-openai-api.
# M3 carries a 1M-token context window; the M2.x line is 204,800 tokens.
_MINIMAX_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("MiniMax-M3 - Latest, 1M ctx, native multimodal", "MiniMax-M3"),
        ("MiniMax-M2.7-highspeed - Fast M2.7, 204K ctx, ~100 TPS", "MiniMax-M2.7-highspeed"),
        ("MiniMax-M2.5-highspeed - Previous-gen highspeed, 204K ctx", "MiniMax-M2.5-highspeed"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("MiniMax-M3 - Latest flagship, 1M ctx, multimodal coding/agent", "MiniMax-M3"),
        ("MiniMax-M2.7 - Previous flagship, 204K ctx", "MiniMax-M2.7"),
        ("MiniMax-M2.7-highspeed - Same quality as M2.7, ~100 TPS", "MiniMax-M2.7-highspeed"),
        ("MiniMax-M2.5 - Earlier flagship, 204K ctx", "MiniMax-M2.5"),
        ("Custom model ID", "custom"),
    ],
}


MODEL_OPTIONS: ProviderModeOptions = {
    "openai": {
        "quick": [
            ("GPT-5.6 Luna - Fastest, cost-sensitive workloads", "gpt-5.6-luna"),
            ("GPT-5.6 Terra - Balanced intelligence and cost", "gpt-5.6-terra"),
            ("GPT-5.4 Mini - Fast, strong coding and tool use", "gpt-5.4-mini"),
            ("GPT-5.4 Nano - Cheapest, high-volume tasks", "gpt-5.4-nano"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("GPT-5.6 Sol - Latest frontier for complex reasoning", "gpt-5.6-sol"),
            ("GPT-5.6 Terra - Balanced frontier reasoning", "gpt-5.6-terra"),
            ("GPT-5.6 Luna - Cost-efficient reasoning", "gpt-5.6-luna"),
            ("GPT-5.5 - Previous frontier, 1M context", "gpt-5.5"),
            ("GPT-5.4 - Previous-gen frontier, 1M context, cost-effective", "gpt-5.4"),
            ("Custom model ID", "custom"),
        ],
    },
    "anthropic": {
        "quick": [
            ("Claude Sonnet 5 - Best speed and intelligence balance", "claude-sonnet-5"),
            ("Claude Haiku 4.5 - Fastest with near-frontier intelligence", "claude-haiku-4-5"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Claude Fable 5 - Most capable, long-running agents", "claude-fable-5"),
            ("Claude Opus 4.8 - Frontier agentic coding and reasoning", "claude-opus-4-8"),
            ("Claude Sonnet 5 - Near-frontier intelligence at Sonnet cost", "claude-sonnet-5"),
            ("Claude Opus 4.7 - Previous frontier, long-running agents", "claude-opus-4-7"),
            ("Custom model ID", "custom"),
        ],
    },
    "google": {
        "quick": [
            ("Gemini 3.7 Flash - Latest, frontier agentic + coding (GA)", "gemini-3.7-flash"),
            ("Gemini 3.6 Flash - Previous stable Flash", "gemini-3.6-flash"),
            ("Gemini 3.5 Flash - Stable agentic and coding model", "gemini-3.5-flash"),
            ("Gemini 3.5 Flash Lite - Most cost-efficient", "gemini-3.5-flash-lite"),
            ("Gemini 3.1 Flash Lite - Stable low-cost alternative", "gemini-3.1-flash-lite"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Gemini 3.1 Pro - Reasoning-first, complex workflows (preview)", "gemini-3.1-pro-preview"),
            ("Gemini 3.7 Flash - Latest GA, strong agentic + coding", "gemini-3.7-flash"),
            ("Gemini 3.6 Flash - Previous stable reasoning model", "gemini-3.6-flash"),
            ("Gemini 3.5 Flash - Stable reasoning model", "gemini-3.5-flash"),
            ("Custom model ID", "custom"),
        ],
    },
    "xai": {
        "quick": [
            ("Grok 4.3 - Latest flagship, fast with built-in reasoning", "grok-4.3"),
            ("Grok 4.20 (Non-Reasoning) - Speed-optimized", "grok-4.20-0309-non-reasoning"),
            ("Grok Build 0.1 - Coding-specialized, 256K ctx", "grok-build-0.1"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Grok 4.3 - Latest flagship, built-in reasoning, 1M ctx", "grok-4.3"),
            ("Grok 4.20 (Reasoning) - Previous-gen reasoning", "grok-4.20-0309-reasoning"),
            ("Grok 4.20 Multi-Agent - Multi-agent reasoning", "grok-4.20-multi-agent-0309"),
            ("Custom model ID", "custom"),
        ],
    },
    # DeepSeek: the deepseek-chat / deepseek-reasoner aliases are deprecated
    # (2026-07-24) and now map to V4 Flash; expose the V4 IDs directly. V4 Flash
    # serves both non-thinking and thinking modes (the DeepSeekChatOpenAI client
    # handles the reasoning_content round-trip).
    "deepseek": {
        "quick": [
            ("DeepSeek V4 Flash - Latest fast model, thinking + non-thinking", "deepseek-v4-flash"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("DeepSeek V4 Pro - Latest flagship", "deepseek-v4-pro"),
            ("DeepSeek V4 Flash - Fast, supports thinking", "deepseek-v4-flash"),
            ("Custom model ID", "custom"),
        ],
    },
    # Qwen: same model IDs across global (dashscope-intl) and China
    # (dashscope) endpoints, so the two provider keys share one model list.
    "qwen": _QWEN_MODELS,
    "qwen-cn": _QWEN_MODELS,
    # GLM: Z.AI (international) and BigModel (China) host the same model
    # IDs; the two provider keys share one model list.
    "glm": _GLM_MODELS,
    "glm-cn": _GLM_MODELS,
    # MiniMax: same model IDs across global (.io) and China (.com) regions,
    # so the two provider keys share one model list.
    "minimax": _MINIMAX_MODELS,
    "minimax-cn": _MINIMAX_MODELS,
    # OpenRouter: fetched dynamically. Azure: any deployed model name.
    # Ollama display labels intentionally omit a "local" marker — the
    # endpoint is now configurable via OLLAMA_BASE_URL, so the same labels
    # apply whether the user runs ollama-serve on localhost or against a
    # remote host. The actual resolved endpoint is surfaced separately by
    # cli.utils.confirm_ollama_endpoint() right after provider selection.
    # "Custom model ID" lets users pick any model they have pulled via
    # `ollama pull` beyond the three suggested defaults.
    "ollama": {
        "quick": [
            ("Qwen3:latest (8B)", "qwen3:latest"),
            ("GPT-OSS:latest (20B)", "gpt-oss:latest"),
            ("GLM-4.7-Flash:latest (30B)", "glm-4.7-flash:latest"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("GLM-4.7-Flash:latest (30B)", "glm-4.7-flash:latest"),
            ("GPT-OSS:latest (20B)", "gpt-oss:latest"),
            ("Qwen3:latest (8B)", "qwen3:latest"),
            ("Custom model ID", "custom"),
        ],
    },
    # Generic OpenAI-compatible endpoint: the model is whatever the user's
    # server serves, so only "Custom model ID" is offered.
    "openai_compatible": _CUSTOM_ONLY,
    # Hosted OpenAI-compatible providers that serve many (and frequently
    # changing) models — offer "Custom model ID" rather than a list that goes
    # stale. The endpoint + key are wired by the provider; the user picks the
    # model their account has access to.
    "mistral": _CUSTOM_ONLY,
    "kimi": _CUSTOM_ONLY,
    "groq": _CUSTOM_ONLY,
    "nvidia": _CUSTOM_ONLY,
    # Bedrock model IDs / cross-region inference profile IDs are user-specified.
    "bedrock": _CUSTOM_ONLY,
}


def get_model_options(provider: str, mode: str) -> list[ModelOption]:
    """Return shared model options for a provider and selection mode."""
    return MODEL_OPTIONS[provider.lower()][mode]


def get_known_models() -> dict[str, list[str]]:
    """Build known model names from the shared CLI catalog."""
    return {
        provider: sorted(
            {
                value
                for options in mode_options.values()
                for _, value in options
            }
        )
        for provider, mode_options in MODEL_OPTIONS.items()
    }


REASONING_CONTROLS: dict[str, dict[str, str | list[str] | None]] = {
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
        "levels": ["low", "medium", "high", "xhigh", "max"],
        "default": "high",
    },
}


def get_reasoning_control(provider: str, model: str) -> dict[str, object] | None:
    """Return model-aware reasoning metadata for web/CLI configuration UIs."""
    provider = provider.lower()
    control = REASONING_CONTROLS.get(provider)
    if control is None:
        return None
    levels = list(control["levels"])
    model = model.lower()
    if provider == "google" and model != "custom" and ("3.7-flash" in model or "pro" in model):
        levels = [level for level in levels if level != "minimal"]
    elif provider == "openai" and model != "custom" and not model.startswith("gpt-5.6"):
        levels = [level for level in levels if level != "max"]
    elif provider == "anthropic" and model != "custom" and not any(
        family in model for family in ("claude-sonnet-5", "claude-fable-5", "claude-opus-5")
    ):
        levels = [level for level in levels if level not in {"xhigh", "max"}]
    default = control["default"]
    if provider == "google" and "flash-lite" in model:
        default = "minimal"
    elif provider == "google" and "pro" in model:
        default = "high"
    return {
        "config_key": control["config_key"],
        "env_var": control["env_var"],
        "label": control["label"],
        "levels": levels,
        "default": default if default in levels else levels[0],
    }

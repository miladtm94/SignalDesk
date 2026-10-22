from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ANALYST_KEYS = {"market", "social", "news", "fundamentals"}


def _compact_symbol(value: str) -> str:
    value = value.strip().upper()
    if not value or any(char.isspace() for char in value):
        raise ValueError("ticker must be a compact exchange symbol")
    return value


class AnalysisSettings(BaseModel):
    """What produces a decision: the analyst desk, models and debate depth."""

    asset_type: Literal["stock", "crypto"] = "stock"
    selected_analysts: list[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"],
        min_length=1,
    )
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_debate_rounds: int = Field(default=1, ge=1, le=10)
    max_risk_discuss_rounds: int = Field(default=1, ge=1, le=10)
    output_language: str = Field(default="English", min_length=2, max_length=40)
    google_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    # Decide against the saved book (Holdings) instead of without portfolio context.
    use_portfolio: bool = False

    @field_validator("selected_analysts")
    @classmethod
    def validate_analysts(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        unknown = set(unique) - ANALYST_KEYS
        if unknown:
            raise ValueError(f"unknown analysts: {', '.join(sorted(unknown))}")
        return unique


class RunCreate(AnalysisSettings):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: date
    checkpoint_enabled: bool = True
    benchmark_ticker: str | None = Field(default=None, max_length=32)
    data_vendors: dict[str, str] | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return _compact_symbol(value)


class RunCreated(BaseModel):
    run_id: str
    status: str
    estimated_cost_usd: float


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    section_key: str
    content_md: str
    structured_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rating: str | None
    action: str | None
    price_target: float | None
    time_horizon: str | None
    confidence: str | None
    entry_price: float | None
    stop_loss: float | None


class UsageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    llm_calls: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    estimated_cost_usd: float
    api_equivalent_usd: float | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note_text: str
    tags: list[str]
    starred: bool
    created_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class RunSummary(BaseModel):
    id: str
    ticker: str
    trade_date: date
    asset_type: str
    selected_analysts: list[str]
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    temperature: float | None
    max_debate_rounds: int
    max_risk_discuss_rounds: int
    checkpoint_enabled: bool
    output_language: str
    estimated_cost_usd: float
    status: str
    error_message: str | None
    starred: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    decision: DecisionOut | None = None
    usage: UsageOut | None = None
    used_portfolio: bool = False


class RunDetail(RunSummary):
    benchmark_ticker: str | None
    config_snapshot: dict[str, Any]
    sections: list[SectionOut]
    notes: list[NoteOut]
    events: list[EventOut]
    portfolio_snapshot: dict[str, Any] | None = None
    # The portfolio block exactly as the trader, risk analysts and portfolio
    # manager read it for this run's ticker.
    portfolio_context: str | None = None


class ChartCandleOut(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float


class StrategyLevelOut(BaseModel):
    kind: Literal["entry", "take_profit", "stop_loss"]
    price: float
    source: str


class RunChartOut(BaseModel):
    symbol: str
    as_of: str
    current_price: float
    candles: list[ChartCandleOut]
    levels: list[StrategyLevelOut]


class RunPatch(BaseModel):
    starred: bool


class NoteCreate(BaseModel):
    note_text: str = Field(min_length=1, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    starred: bool = False

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip().lower() for tag in value if tag.strip()))


class WatchlistCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    notes: str = Field(default="", max_length=2_000)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    notes: str
    added_at: datetime


class SecretWrite(BaseModel):
    name: str
    value: str = Field(min_length=1, max_length=10_000)


class SecretMask(BaseModel):
    name: str
    configured: bool
    masked_value: str | None


class PreferencesWrite(BaseModel):
    selected_analysts: list[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"], min_length=1
    )
    llm_provider: str | None = None
    deep_think_llm: str | None = None
    quick_think_llm: str | None = None
    max_debate_rounds: int = Field(default=1, ge=1, le=10)
    max_risk_discuss_rounds: int = Field(default=1, ge=1, le=10)
    checkpoint_enabled: bool = True
    output_language: str = Field(default="English", min_length=2, max_length=40)
    google_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    custom_models: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("selected_analysts")
    @classmethod
    def validate_preference_analysts(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        if set(unique) - ANALYST_KEYS:
            raise ValueError("Preferences include an unknown analyst")
        return unique

    @field_validator("custom_models")
    @classmethod
    def clean_custom_models(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        cleaned: dict[str, list[str]] = {}
        for provider, models in value.items():
            provider_id = provider.strip().lower()
            if not provider_id:
                continue
            valid = [model.strip() for model in models if model.strip() and not any(char.isspace() for char in model.strip())]
            cleaned[provider_id] = list(dict.fromkeys(valid))[:50]
        return cleaned


class PositionIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    quantity: float = Field(description="Signed units held; negative is short")
    average_price: float | None = Field(default=None, gt=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return _compact_symbol(value)

    @field_validator("quantity")
    @classmethod
    def held(cls, value: float) -> float:
        if value == 0:
            raise ValueError("a position needs a non-zero quantity; remove it to show none is held")
        return value


class PortfolioWrite(BaseModel):
    """The caller's book: an empty positions list is a flat book, not a missing one."""

    cash: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=12)
    positions: list[PositionIn] = Field(default_factory=list, max_length=200)

    @field_validator("currency")
    @classmethod
    def clean_currency(cls, value: str | None) -> str | None:
        value = (value or "").strip().upper()
        return value or None

    @field_validator("positions")
    @classmethod
    def one_row_per_ticker(cls, value: list[PositionIn]) -> list[PositionIn]:
        seen: set[str] = set()
        for position in value:
            if position.ticker in seen:
                raise ValueError(f"{position.ticker} is listed twice; combine it into one position")
            seen.add(position.ticker)
        return value


class PortfolioOut(BaseModel):
    configured: bool
    cash: float | None = None
    currency: str | None = None
    positions: list[PositionIn] = Field(default_factory=list)
    updated_at: datetime | None = None


class PortfolioPreview(BaseModel):
    ticker: str
    held: PositionIn | None
    context: str


class PortfolioQuote(BaseModel):
    ticker: str
    last_close: float | None
    as_of: str | None
    error: str | None = None

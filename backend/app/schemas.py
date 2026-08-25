from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ANALYST_KEYS = {"market", "social", "news", "fundamentals"}


class RunCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: date
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
    checkpoint_enabled: bool = True
    output_language: str = Field(default="English", min_length=2, max_length=40)
    benchmark_ticker: str | None = Field(default=None, max_length=32)
    data_vendors: dict[str, str] | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value or any(char.isspace() for char in value):
            raise ValueError("ticker must be a compact exchange symbol")
        return value

    @field_validator("selected_analysts")
    @classmethod
    def validate_analysts(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        unknown = set(unique) - ANALYST_KEYS
        if unknown:
            raise ValueError(f"unknown analysts: {', '.join(sorted(unknown))}")
        return unique


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


class RunDetail(RunSummary):
    benchmark_ticker: str | None
    config_snapshot: dict[str, Any]
    sections: list[SectionOut]
    notes: list[NoteOut]
    events: list[EventOut]


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

    @field_validator("selected_analysts")
    @classmethod
    def validate_preference_analysts(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        if set(unique) - ANALYST_KEYS:
            raise ValueError("Preferences include an unknown analyst")
        return unique

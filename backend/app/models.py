from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("idx_runs_ticker_created", "ticker", "created_at"),
        Index("idx_runs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    asset_type: Mapped[str] = mapped_column(String(20), default="stock")
    selected_analysts: Mapped[list[str]] = mapped_column(JSON)
    llm_provider: Mapped[str] = mapped_column(String(40))
    deep_think_llm: Mapped[str] = mapped_column(String(160))
    quick_think_llm: Mapped[str] = mapped_column(String(160))
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_debate_rounds: Mapped[int] = mapped_column(Integer, default=1)
    max_risk_discuss_rounds: Mapped[int] = mapped_column(Integer, default=1)
    checkpoint_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    output_language: Mapped[str] = mapped_column(String(40), default="English")
    benchmark_ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The book the decision agents saw, exactly as launched; None means the run
    # had no portfolio context (which is not the same as a flat book).
    portfolio_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Set when the run is one cell of a backtest grid.
    backtest_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    sections: Mapped[list[RunSection]] = relationship(cascade="all, delete-orphan", back_populates="run")
    decision: Mapped[Decision | None] = relationship(cascade="all, delete-orphan", back_populates="run", uselist=False)
    usage: Mapped[Usage | None] = relationship(cascade="all, delete-orphan", back_populates="run", uselist=False)
    notes: Mapped[list[RunNote]] = relationship(cascade="all, delete-orphan", back_populates="run")
    events: Mapped[list[RunEvent]] = relationship(cascade="all, delete-orphan", back_populates="run")


class RunSection(Base):
    __tablename__ = "run_sections"
    __table_args__ = (
        UniqueConstraint("run_id", "section_key", name="uq_run_sections_run_key"),
        Index("idx_run_sections_run_created", "run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(String(40))
    content_md: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    run: Mapped[Run] = relationship(back_populates="sections")


class Decision(Base):
    __tablename__ = "decisions"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    rating: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    price_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_horizon: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped[Run] = relationship(back_populates="decision")


class Usage(Base):
    __tablename__ = "usage"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    # For a run billed to a Claude plan: what its tokens would cost at API list
    # prices, as Claude Code reports it. The plan itself bills nothing per call.
    api_equivalent_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    run: Mapped[Run] = relationship(back_populates="usage")


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunNote(Base):
    __tablename__ = "run_notes"
    __table_args__ = (Index("idx_run_notes_run_created", "run_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    note_text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="notes")


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("idx_run_events_run_sequence", "run_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="events")


class Secret(Base):
    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AppPreference(Base):
    __tablename__ = "app_preferences"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Backtest(Base):
    """A grid of (ticker, date) analyses scored against what each instrument did next."""

    __tablename__ = "backtests"
    __table_args__ = (Index("idx_backtests_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tickers: Mapped[list[str]] = mapped_column(JSON)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    every_n_days: Mapped[int] = mapped_column(Integer)
    dates: Mapped[list[str]] = mapped_column(JSON)
    asset_type: Mapped[str] = mapped_column(String(20), default="stock")
    selected_analysts: Mapped[list[str]] = mapped_column(JSON)
    llm_provider: Mapped[str] = mapped_column(String(40))
    deep_think_llm: Mapped[str] = mapped_column(String(160))
    quick_think_llm: Mapped[str] = mapped_column(String(160))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    portfolio_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cells: Mapped[list[BacktestCell]] = relationship(
        cascade="all, delete-orphan", back_populates="backtest", order_by="BacktestCell.position"
    )


class BacktestCell(Base):
    """One (ticker, date) of a backtest: the run that decided it and how it turned out."""

    __tablename__ = "backtest_cells"
    __table_args__ = (
        UniqueConstraint("backtest_id", "ticker", "trade_date", name="uq_backtest_cells_grid"),
        Index("idx_backtest_cells_backtest_position", "backtest_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    backtest_id: Mapped[str] = mapped_column(ForeignKey("backtests.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    ticker: Mapped[str] = mapped_column(String(32))
    trade_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rating: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # True until the decision's holding window has traded and it was scored.
    pending: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    benchmark: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    backtest: Mapped[Backtest] = relationship(back_populates="cells")

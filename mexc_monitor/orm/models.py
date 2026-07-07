from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mexc_monitor.orm.base import Base


class SpreadSnapshot(Base):
    """Строка снимка спреда в SQLite (исторический лог)."""

    __tablename__ = "spread_snapshots"
    __table_args__ = (
        Index("idx_spread_observed", "observed_at"),
        Index("idx_spread_market_sym_time", "market", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observed_at: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    bid_qty: Mapped[float] = mapped_column(Float, nullable=False)
    ask_qty: Mapped[float] = mapped_column(Float, nullable=False)
    mid: Mapped[float] = mapped_column(Float, nullable=False)
    spread_abs: Mapped[float] = mapped_column(Float, nullable=False)
    spread_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h_base: Mapped[float] = mapped_column(Float, nullable=False)
    volume_24h_quote: Mapped[float] = mapped_column(Float, nullable=False)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_round_trip_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_spread_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    l1_max_executable_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    l1_max_notional_quote: Mapped[float | None] = mapped_column(Float, nullable=True)



class CrossSpreadSnapshot(Base):
    """Снимок межбиржевого спреда MEXC ↔ AsterDEX."""

    __tablename__ = "cross_spread_snapshots"
    __table_args__ = (
        Index("idx_cross_observed", "observed_at"),
        Index("idx_cross_sym_time", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    mexc_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    mexc_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    mexc_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    aster_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    aster_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    aster_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    basis_abs: Mapped[float | None] = mapped_column(Float, nullable=True)
    basis_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[str] = mapped_column(Text, nullable=False)

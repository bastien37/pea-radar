from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # révocation des sessions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Scan(Base):
    __tablename__ = "scans"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|failed
    progress: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ranking: Mapped[list] = mapped_column(JSON, default=list)  # classement complet de l'univers
    opportunities: Mapped[list["Opportunity"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", order_by="Opportunity.rank"
    )


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[float] = mapped_column(Float)
    price_date: Mapped[str] = mapped_column(String(10))
    score: Mapped[float] = mapped_column(Float)
    adjusted_score: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[int] = mapped_column(Integer)  # 1 (faible) à 5 (très élevé)
    expected_gain_pct: Mapped[float] = mapped_column(Float)
    gain_low_pct: Mapped[float] = mapped_column(Float)
    gain_high_pct: Mapped[float] = mapped_column(Float)
    win_rate: Mapped[float] = mapped_column(Float)
    holding_days: Mapped[int] = mapped_column(Integer)  # jours de bourse
    stop_loss: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # indicateurs, sous-scores, étude
    news: Mapped[dict] = mapped_column(JSON, default=dict)  # analyse IA des actualités
    scan: Mapped[Scan] = relationship(back_populates="opportunities")


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    initial_cash: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    positions: Mapped[list["Position"]] = relationship(cascade="all, delete-orphan")
    trades: Mapped[list["Trade"]] = relationship(cascade="all, delete-orphan")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "ticker"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    ticker: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    avg_cost: Mapped[float] = mapped_column(Float)  # prix de revient unitaire, frais inclus


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    ticker: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))  # buy|sell
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

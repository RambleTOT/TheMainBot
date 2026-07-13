"""Модели БД и доступ к сессиям.

По умолчанию — SQLite (для разработки). Для production укажите PostgreSQL
в DATABASE_URL (postgresql+asyncpg://...). Схема при первом запуске создаётся
автоматически; для боевого проекта рекомендуется Alembic-миграции.

ВНИМАНИЕ (dev): при добавлении новых колонок к существующим таблицам SQLite
не мигрирует автоматически — на этапе разработки просто удалите файл bot.db,
он пересоздастся. В production используется PostgreSQL + миграции.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_config


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # бан из админки
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    tariff_id: Mapped[str] = mapped_column(String(32))  # код тарифа
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | expired | cancelled
    is_forever: Mapped[bool] = mapped_column(Boolean, default=False)
    autorenew: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = бессрочно
    notified_expiring: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    tariff_id: Mapped[str] = mapped_column(String(32))
    amount_kop: Mapped[int] = mapped_column(Integer)  # сумма в копейках
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | succeeded | failed
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False)  # покупка «в подарок»
    gift_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GiftCode(Base):
    """Подарочная подписка: покупатель оплачивает, друг активирует по одноразовой ссылке."""
    __tablename__ = "gift_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tariff_code: Mapped[str] = mapped_column(String(32))
    buyer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(16), default="paid")  # paid | redeemed
    redeemed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Tariff(Base):
    """Тариф (редактируется из админки)."""
    __tablename__ = "tariffs"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    emoji: Mapped[str] = mapped_column(String(16), default="")
    price_rub: Mapped[int] = mapped_column(Integer)
    months: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = «Навсегда»
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(Base):
    """Тексты и настройки бота (редактируются из админки), ключ-значение."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_config().database_url, echo=False, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _session_factory


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

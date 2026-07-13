"""Бизнес-логика: пользователи, подписки, автопродление, подарки, время МСК."""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from dateutil.relativedelta import relativedelta
from sqlalchemy import select, update

from . import content
from .content import TariffDTO
from .database import GiftCode, Payment, Subscription, User, get_sessionmaker

log = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
UTC = ZoneInfo("UTC")


# ------------------------- форматирование -------------------------

def fmt_msk(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def fmt_price(rub: int) -> str:
    return f"{rub:,}".replace(",", " ")


def duration_text(tariff: TariffDTO) -> str:
    if tariff.is_forever:
        return "безлимитный"
    return {1: "1 месяц", 3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев"}.get(
        tariff.months, f"{tariff.months} мес."
    )


# ------------------------- снимок подписки -------------------------

@dataclass
class SubInfo:
    id: int
    tariff_id: str
    is_forever: bool
    autorenew: bool
    expires_at: datetime | None
    status: str


def _to_info(sub: Subscription) -> SubInfo:
    return SubInfo(sub.id, sub.tariff_id, sub.is_forever, sub.autorenew, sub.expires_at, sub.status)


def _is_active(sub: Subscription, now: datetime) -> bool:
    if sub.status != "active":
        return False
    if sub.is_forever:
        return True
    return sub.expires_at is not None and sub.expires_at > now


# ------------------------- пользователи -------------------------

async def upsert_user(tg_id: int, username: str | None, full_name: str | None) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, tg_id)
        if u is None:
            s.add(User(tg_id=tg_id, username=username, full_name=full_name))
        else:
            u.username = username
            u.full_name = full_name
        await s.commit()


async def ensure_user(tg_id: int) -> None:
    """Создаёт пользователя, если его ещё нет (не затирает существующие данные)."""
    sm = get_sessionmaker()
    async with sm() as s:
        if await s.get(User, tg_id) is None:
            s.add(User(tg_id=tg_id))
            await s.commit()


async def is_blocked(tg_id: int) -> bool:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, tg_id)
        return bool(u and u.is_blocked)


# ------------------------- подписки -------------------------

async def get_active_subscription(tg_id: int) -> SubInfo | None:
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(
            select(Subscription)
            .where(Subscription.user_id == tg_id, Subscription.status == "active")
            .order_by(Subscription.id.desc())
        )
        now = datetime.utcnow()
        for sub in res.scalars():
            if _is_active(sub, now):
                return _to_info(sub)
        return None


async def activate_subscription(
    tg_id: int,
    tariff: TariffDTO,
    *,
    is_recurring: bool = False,
    autorenew: bool = True,
    provider: str = "mock",
    provider_payment_id: str | None = None,
    record_payment: bool = True,
) -> SubInfo:
    """Активирует или продлевает подписку после успешной оплаты.

    Правила стэкинга:
      - «Навсегда» поверх любой подписки → делает её вечной, автопродление off.
      - срочный тариф при активной срочной → продлевает срок (суммирует).
      - срочный тариф при вечной → ничего не меняет (уже вечная).
    """
    sm = get_sessionmaker()
    async with sm() as s:
        now = datetime.utcnow()
        res = await s.execute(
            select(Subscription)
            .where(Subscription.user_id == tg_id, Subscription.status == "active")
            .order_by(Subscription.id.desc())
        )
        active_rows = list(res.scalars())
        current = next((sub for sub in active_rows if _is_active(sub, now)), None)

        if tariff.is_forever:
            if current:
                current.is_forever = True
                current.expires_at = None
                current.autorenew = False
                current.tariff_id = tariff.code
                target = current
            else:
                target = Subscription(
                    user_id=tg_id, tariff_id=tariff.code, is_forever=True,
                    expires_at=None, autorenew=False, status="active",
                )
                s.add(target)
        else:
            if current and current.is_forever:
                target = current  # уже вечная — оставляем как есть
            elif current and current.expires_at and current.expires_at > now:
                current.expires_at = current.expires_at + relativedelta(months=tariff.months)
                current.tariff_id = tariff.code
                # никогда не выключаем чужой автоплатёж при продлении (например, подарком)
                current.autorenew = current.autorenew or autorenew
                current.notified_expiring = False
                target = current
            else:
                target = Subscription(
                    user_id=tg_id, tariff_id=tariff.code, is_forever=False,
                    expires_at=now + relativedelta(months=tariff.months),
                    autorenew=autorenew, status="active",
                )
                s.add(target)

        # Реконсиляция: закрываем любые другие «active»-строки пользователя (в т.ч.
        # просроченные, но ещё не помеченные expired), чтобы планировщик их не воскрешал.
        for sub in active_rows:
            if sub is not target:
                sub.status = "expired"

        if record_payment:
            s.add(Payment(
                user_id=tg_id, tariff_id=tariff.code, amount_kop=tariff.price_rub * 100,
                provider=provider, provider_payment_id=provider_payment_id,
                status="succeeded", is_recurring=is_recurring,
            ))
        await s.commit()
        return _to_info(target)


async def stop_autorenew(tg_id: int) -> bool:
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(
            select(Subscription)
            .where(Subscription.user_id == tg_id, Subscription.status == "active")
            .order_by(Subscription.id.desc())
        )
        now = datetime.utcnow()
        sub = next((x for x in res.scalars() if _is_active(x, now)), None)
        if sub is None:
            return False
        sub.autorenew = False
        await s.commit()
        return True


# ------------------------- подарочные подписки -------------------------

async def create_gift_code(
    buyer_id: int, tariff: TariffDTO, *, provider: str = "mock",
    provider_payment_id: str | None = None,
) -> str:
    """Регистрирует оплаченный подарок и возвращает одноразовый код для друга."""
    code = secrets.token_urlsafe(9)
    sm = get_sessionmaker()
    async with sm() as s:
        s.add(GiftCode(code=code, tariff_code=tariff.code, buyer_id=buyer_id, status="paid"))
        s.add(Payment(
            user_id=buyer_id, tariff_id=tariff.code, amount_kop=tariff.price_rub * 100,
            provider=provider, provider_payment_id=provider_payment_id,
            status="succeeded", is_recurring=False, is_gift=True, gift_code=code,
        ))
        await s.commit()
    return code


async def _revert_gift(code: str, redeemer_id: int) -> None:
    """Возвращает подарок в состояние 'paid', если активация не удалась."""
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            update(GiftCode)
            .where(GiftCode.code == code, GiftCode.redeemed_by == redeemer_id)
            .values(status="paid", redeemed_by=None, redeemed_at=None)
        )
        await s.commit()


async def redeem_gift(code: str, redeemer_id: int) -> tuple[SubInfo, TariffDTO] | None:
    """Активирует подарок для друга. Возвращает (подписка, тариф) или None.

    Захват кода — атомарным UPDATE ... WHERE status='paid' с проверкой rowcount,
    поэтому одновременное/повторное использование одной ссылки не даёт двойную выдачу.
    """
    sm = get_sessionmaker()
    async with sm() as s:
        now = datetime.utcnow()
        res = await s.execute(
            update(GiftCode)
            .where(GiftCode.code == code, GiftCode.status == "paid")
            .values(status="redeemed", redeemed_by=redeemer_id, redeemed_at=now)
        )
        await s.commit()
        if res.rowcount != 1:
            return None  # код не найден или уже использован
        gift = (await s.execute(select(GiftCode).where(GiftCode.code == code))).scalar_one()
        tariff_code = gift.tariff_code

    tariff = await content.get_tariff(tariff_code)
    if tariff is None:
        await _revert_gift(code, redeemer_id)
        return None

    # Подарок не имеет привязанной карты друга → без автопродления, без нового платежа.
    try:
        sub = await activate_subscription(
            redeemer_id, tariff, autorenew=False, provider="gift", record_payment=False,
        )
    except Exception:  # noqa: BLE001
        await _revert_gift(code, redeemer_id)
        raise
    return sub, tariff


# ------------------------- задачи планировщика -------------------------

async def expire_due(bot: Bot) -> None:
    """Обрабатывает подписки, у которых истёк срок.

    autorenew=True  → автопродление (ДЕМО: считаем списание успешным и продлеваем).
                      В production здесь реальное списание через провайдера; при неуспехе —
                      немедленный отзыв доступа (решение заказчика: без попыток и grace).
    autorenew=False → подписка истекает, доступ отзывается, уведомляем пользователя.
    """
    from .access import revoke_access

    sm = get_sessionmaker()
    renewed: list[tuple[int, datetime]] = []
    expired: list[int] = []

    async with sm() as s:
        now = datetime.utcnow()
        res = await s.execute(
            select(Subscription).where(
                Subscription.status == "active",
                Subscription.is_forever.is_(False),
                Subscription.expires_at.is_not(None),
                Subscription.expires_at <= now,
            )
        )
        subs = list(res.scalars())
        # заблокированных из админки не продлеваем/не списываем
        blocked_ids: set[int] = set()
        if subs:
            bres = await s.execute(
                select(User.tg_id).where(
                    User.tg_id.in_({x.user_id for x in subs}), User.is_blocked.is_(True)
                )
            )
            blocked_ids = {row[0] for row in bres.all()}
        tariffs: dict[str, TariffDTO | None] = {}
        for sub in subs:
            if sub.tariff_id not in tariffs:
                tariffs[sub.tariff_id] = await content.get_tariff(sub.tariff_id)
            tariff = tariffs[sub.tariff_id]
            if sub.autorenew and tariff and not tariff.is_forever and sub.user_id not in blocked_ids:
                # продвигаем срок за «сейчас» за один шаг — иначе при простое бота
                # строка продлевалась бы (и списывалась бы) на каждом тике.
                new_exp = sub.expires_at
                while new_exp <= now:
                    new_exp = new_exp + relativedelta(months=tariff.months)
                sub.expires_at = new_exp
                sub.notified_expiring = False
                renewed.append((sub.user_id, sub.expires_at))
            else:
                sub.status = "expired"
                expired.append(sub.user_id)
        await s.commit()

    for uid, exp in renewed:
        try:
            await bot.send_message(uid, await content.get_text("sub_renewed", date=fmt_msk(exp)))
        except Exception:  # noqa: BLE001
            log.exception("Не удалось уведомить о продлении user=%s", uid)

    for uid in expired:
        try:
            await revoke_access(bot, uid)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка отзыва доступа user=%s", uid)
        try:
            await bot.send_message(uid, await content.get_text("sub_expired"))
        except Exception:  # noqa: BLE001
            log.exception("Не удалось уведомить об окончании user=%s", uid)

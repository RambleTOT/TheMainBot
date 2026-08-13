"""Бизнес-логика: пользователи, подписки, автопродление, подарки, время МСК."""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from dateutil.relativedelta import relativedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import cloudpayments, content
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


async def _apply_activation(s, tg_id: int, tariff: TariffDTO, *, autorenew: bool) -> Subscription:
    """Стэкинг подписки в ПЕРЕДАННОЙ сессии, БЕЗ commit (вызывающий коммитит сам).

    Вынесено отдельно, чтобы приём вебхука мог выдать подписку и записать платёж-маркер
    идемпотентности В ОДНОЙ транзакции (атомарно). Правила стэкинга:
      - «Навсегда» поверх любой подписки → делает её вечной, автопродление off.
      - срочный тариф при активной срочной → продлевает срок (суммирует).
      - срочный тариф при вечной → ничего не меняет (уже вечная).
    """
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
    return target


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
    """Активирует или продлевает подписку после успешной оплаты (одна транзакция)."""
    sm = get_sessionmaker()
    async with sm() as s:
        target = await _apply_activation(s, tg_id, tariff, autorenew=autorenew)
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


async def get_card_token(tg_id: int) -> str | None:
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, tg_id)
        return u.card_token if u else None


async def activate_from_webhook(uid: int, tariff_code: str, cp_tx_id: str, *,
                                token: str | None = None, is_gift: bool = False,
                                email: str | None = None) -> dict:
    """Идемпотентно и АТОМАРНО обрабатывает успешную оплату CloudPayments (вебхук Pay).

    Ключевое: платёж-маркер с УНИКАЛЬНЫМ cp_transaction_id, выдача подписки и запись
    токена карты коммитятся В ОДНОЙ транзакции. Поэтому:
      - повторная доставка того же вебхука → IntegrityError → откат всей единицы → 'duplicate'
        (второй раз подписка НЕ продлевается);
      - если выдача упадёт по временной ошибке — не коммитится и маркер, значит повторный
        вебхук сможет корректно повторить активацию (нет «оплатил, но доступа нет навсегда»).
    """
    tariff = await content.get_tariff(tariff_code)
    if tariff is None:
        return {"status": "no_tariff"}
    await ensure_user(uid)
    gift_code = secrets.token_urlsafe(9) if is_gift else None
    sm = get_sessionmaker()
    async with sm() as s:
        # маркер идемпотентности + (для не-подарка) сам платёж
        s.add(Payment(user_id=uid, tariff_id=tariff.code, amount_kop=tariff.price_rub * 100,
                      provider="cloudpayments", provider_payment_id=cp_tx_id, cp_transaction_id=cp_tx_id,
                      status="succeeded", is_recurring=(not is_gift and not tariff.is_forever),
                      is_gift=is_gift, gift_code=gift_code))
        if is_gift:
            s.add(GiftCode(code=gift_code, tariff_code=tariff.code, buyer_id=uid, status="paid"))
        try:
            await s.flush()  # здесь ловим дубль cp_transaction_id ДО выдачи доступа
        except IntegrityError:
            await s.rollback()
            return {"status": "duplicate"}

        if is_gift:
            await s.commit()
            return {"status": "gift", "code": gift_code, "tariff": tariff}

        # выдача подписки — в ТОЙ ЖЕ транзакции, что и маркер
        target = await _apply_activation(s, uid, tariff, autorenew=True)
        u = await s.get(User, uid)
        if u:
            if token and not tariff.is_forever:
                u.card_token = token            # для автосписаний
            if email:
                u.email = email                 # для чеков 54-ФЗ при автопродлении
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            return {"status": "duplicate"}
        return {"status": "activated", "sub": _to_info(target), "tariff": tariff}


# ------------------------- задачи планировщика -------------------------

async def _mark_expired(sm, sub_id: int, now: datetime) -> bool:
    """Помечает подписку истёкшей — только если она ВСЁ ЕЩЁ активна и просрочена.

    Пере-проверка защищает от гонки: если между снимком и этим моментом пользователь
    доплатил/продлил (webhook активировал новую подписку), мы не обнулим свежую.
    """
    async with sm() as s:
        sub = await s.get(Subscription, sub_id)
        if sub and sub.status == "active" and not sub.is_forever and sub.expires_at and sub.expires_at <= now:
            sub.status = "expired"
            await s.commit()
            return True
    return False


async def _persist_renewal(sm, sub_id: int, tariff: TariffDTO, new_exp: datetime,
                           charge_tx: str | None) -> bool:
    """Атомарно фиксирует успешное автопродление: новый срок + (опц.) платёж — в ОДНОМ commit.

    Идемпотентно по cp_transaction_id: если этот платёж уже записан (наш прошлый тик успел),
    срок повторно не двигаем. Возвращает True, если срок реально продлён этим вызовом.
    """
    async with sm() as s:
        sub = await s.get(Subscription, sub_id)
        if sub is None or sub.status != "active" or sub.is_forever:
            return False
        if charge_tx:
            already = await s.scalar(select(Payment.id).where(Payment.cp_transaction_id == charge_tx))
            if already:
                return False  # это списание уже учтено — срок двигали тогда же
            s.add(Payment(
                user_id=sub.user_id, tariff_id=tariff.code, amount_kop=tariff.price_rub * 100,
                provider="cloudpayments", provider_payment_id=charge_tx, cp_transaction_id=charge_tx,
                status="succeeded", is_recurring=True,
            ))
        sub.expires_at = new_exp
        sub.notified_expiring = False
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            return False
        return True


async def expire_due(bot: Bot) -> None:
    """Обрабатывает подписки, у которых истёк срок.

    autorenew=True  → автопродление. Если CloudPayments подключён (cp_enabled) — реальное
                      списание по сохранённому токену карты; при неуспехе или отсутствии
                      токена доступ отзывается (решение заказчика: без попыток и grace).
                      Без ключей CP — ДЕМО: продление считается успешным.
    autorenew=False → подписка истекает, доступ отзывается, уведомляем пользователя.

    Списание идёт ВНЕ открытой транзакции, а новый срок фиксируется отдельным атомарным
    commit сразу после успешного списания (+ детерминированный InvoiceId) — чтобы сбой/рестарт
    между «деньги ушли» и «срок записан» не приводил к повторному списанию на следующем тике.
    """
    from .access import revoke_access
    from .config import get_config

    cfg = get_config()
    sm = get_sessionmaker()
    renewed: list[tuple[int, datetime]] = []
    expired: list[int] = []

    # 1) Снимок «просроченных» — короткая транзакция, без сетевых вызовов внутри.
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
        blocked_ids: set[int] = set()
        tokens: dict[int, str | None] = {}
        emails: dict[int, str | None] = {}
        if subs:
            bres = await s.execute(
                select(User.tg_id, User.is_blocked, User.card_token, User.email).where(
                    User.tg_id.in_({x.user_id for x in subs})
                )
            )
            for tg_id, is_blocked, card_token, mail in bres.all():
                if is_blocked:
                    blocked_ids.add(tg_id)
                tokens[tg_id] = card_token
                emails[tg_id] = mail
        # снимок нужных полей: дальше сессия закрыта, ORM-объекты не держим
        snapshot = [(x.id, x.user_id, x.tariff_id, x.expires_at, x.autorenew) for x in subs]

    # 2) Обработка по одной — без открытой транзакции во время сетевых списаний.
    tariffs: dict[str, TariffDTO | None] = {}
    for sub_id, user_id, tariff_id, expires_at, autorenew in snapshot:
        if tariff_id not in tariffs:
            tariffs[tariff_id] = await content.get_tariff(tariff_id)
        tariff = tariffs[tariff_id]
        # months<=0 (например, ошибочно введённый 0) — НЕ продлеваем: иначе цикл ниже был бы вечным
        renewable = (autorenew and tariff and not tariff.is_forever
                     and (tariff.months or 0) > 0 and user_id not in blocked_ids)
        if not renewable:
            if await _mark_expired(sm, sub_id, now):
                expired.append(user_id)
            continue

        charged_ok = True
        charge_tx: str | None = None
        if cfg.cp_enabled:
            token = tokens.get(user_id)
            if not token:
                charged_ok = False  # карта не сохранена — продлить нечем
            else:
                # детерминированный InvoiceId за конкретный период — чтобы CloudPayments
                # отсёк повторное списание, если наш commit не успел записаться.
                invoice_id = f"renew-{sub_id}-{expires_at:%Y%m%d%H%M}"
                result = await cloudpayments.charge_token(
                    amount_rub=tariff.price_rub, account_id=user_id, token=token,
                    description=f"Автопродление подписки — {tariff.title}", label=tariff.title,
                    email=emails.get(user_id), invoice_id=invoice_id,
                )
                charged_ok = result["ok"]
                if charged_ok:
                    model = result["raw"].get("Model") if isinstance(result["raw"], dict) else None
                    charge_tx = (str((model or {}).get("TransactionId") or "") or None) if isinstance(model, dict) else None
                else:
                    log.warning("Автосписание не прошло user=%s tariff=%s: %s",
                                user_id, tariff.code, result.get("raw"))

        if charged_ok:
            new_exp = expires_at
            while new_exp <= now:
                new_exp = new_exp + relativedelta(months=tariff.months)
            if await _persist_renewal(sm, sub_id, tariff, new_exp, charge_tx):
                renewed.append((user_id, new_exp))
        else:
            if await _mark_expired(sm, sub_id, now):
                expired.append(user_id)

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

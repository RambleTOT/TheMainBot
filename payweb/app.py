"""Публичный платёжный сервис: страница виджета CloudPayments + приём вебхуков.

Запускается ОТДЕЛЬНО от админки (127.0.0.1:8090), nginx проксирует на него
пути /pay и /cloudpayments/. Всё, что меняет подписки, идёт только через
вебхуки CloudPayments с проверкой подписи HMAC (заголовок Content-HMAC), а не
по действиям пользователя в браузере.

Идемпотентность: активация в services.activate_from_webhook защищена уникальным
cp_transaction_id, поэтому повторная доставка вебхука не выдаёт доступ дважды.
"""
import json
import logging
import os
from html import escape as _esc
from urllib.parse import parse_qs

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from bot import cloudpayments, content, services
from bot import keyboards as kb
from bot.access import grant_access
from bot.config import get_config
from bot.database import init_db

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_bot: Bot | None = None


def _get_bot() -> Bot:
    """Один общий экземпляр Bot для уведомлений (тот же токен, что у поллинга)."""
    global _bot
    if _bot is None:
        _bot = Bot(get_config().bot_token)
    return _bot


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _bot is not None:
        await _bot.session.close()


# ------------------------- страница оплаты -------------------------

@app.get("/pay", response_class=HTMLResponse)
async def pay_page(request: Request, t: str = ""):
    cfg = get_config()
    payload = cloudpayments.load_pay(t) if t else None
    if not cfg.cp_enabled or not payload:
        return HTMLResponse("<h1>Ссылка недействительна или устарела</h1>", status_code=400)
    tariff = await content.get_tariff(str(payload.get("code", "")))
    if tariff is None or not tariff.is_active:
        return HTMLResponse("<h1>Тариф недоступен</h1>", status_code=404)
    # Глобальный стоп-продаж должен действовать и на платёжной странице (а не только в
    # боте): иначе по уже выданной подписанной ссылке можно оплатить после закрытия продаж.
    if not await content.sales_enabled():
        closed = await content.get_text("sales_closed")
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Продажи закрыты</title>"
                            f"<div style='font:16px sans-serif;max-width:420px;margin:20vh auto;"
                            f"text-align:center'>{_esc(closed)}</div>", status_code=403)

    uid = int(payload["uid"])
    is_gift = bool(payload.get("gift"))
    receipt = cloudpayments.build_receipt(label=tariff.title, amount_rub=tariff.price_rub, email=None)
    # Data возвращается в вебхуке Pay без изменений — по нему активируем нужный тариф.
    data = {
        "tariff": tariff.code,
        "uid": uid,
        "gift": is_gift,
        "CloudPayments": {"CustomerReceipt": receipt},
    }
    invoice_id = f"{'gift' if is_gift else 'sub'}-{uid}-{tariff.code}"
    descr = f"Подписка «{tariff.title}»" + (" (в подарок)" if is_gift else "")
    return templates.TemplateResponse(request, "pay.html", {
        "public_id": cfg.cp_public_id,
        "amount": tariff.price_rub,
        "account_id": str(uid),
        "invoice_id": invoice_id,
        "description": descr,
        "title": tariff.title,
        "is_gift": is_gift,
        "data_json": json.dumps(data, ensure_ascii=False),
        "bot_url": _bot_return_url(),
    })


def _bot_return_url() -> str:
    username = os.getenv("BOT_USERNAME") or ""
    return f"https://t.me/{username}" if username else "https://t.me"


# ------------------------- вебхуки CloudPayments -------------------------

async def _read_verified(request: Request) -> dict | None:
    """Читает сырое тело, проверяет HMAC, возвращает поля формы (или None)."""
    raw = await request.body()
    sig = request.headers.get("Content-HMAC") or request.headers.get("X-Content-HMAC")
    if not cloudpayments.hmac_ok(raw, sig):
        log.warning("CloudPayments webhook: неверная подпись")
        return None
    parsed = parse_qs(raw.decode("utf-8"))
    return {k: v[0] for k, v in parsed.items()}


def _custom_data(fields: dict) -> dict:
    try:
        return json.loads(fields.get("Data") or "{}")
    except (ValueError, TypeError):
        return {}


@app.post("/cloudpayments/check")
async def cp_check(request: Request):
    """Проверка перед списанием: подтверждаем сумму и существование тарифа."""
    fields = await _read_verified(request)
    if fields is None:
        return JSONResponse({"code": 13})
    # стоп-продаж: отклоняем ДО списания (Check приходит до захвата денег)
    if not await content.sales_enabled():
        return JSONResponse({"code": 13})
    custom = _custom_data(fields)
    tariff = await content.get_tariff(str(custom.get("tariff", "")))
    try:
        amount = float(fields.get("Amount") or 0)
    except ValueError:
        amount = 0.0
    if tariff is None or abs(amount - tariff.price_rub) > 0.01:
        return JSONResponse({"code": 13})  # некорректная сумма/тариф — отклонить
    return JSONResponse({"code": 0})


@app.post("/cloudpayments/pay")
async def cp_pay(request: Request):
    """Успешная оплата: идемпотентно активируем подписку и уведомляем пользователя."""
    fields = await _read_verified(request)
    if fields is None:
        return JSONResponse({"code": 13})
    custom = _custom_data(fields)
    tariff_code = str(custom.get("tariff", ""))
    is_gift = bool(custom.get("gift"))
    try:
        # uid из нашего Data приоритетнее AccountId (оба приходят из виджета,
        # но uid мы кладём сами на серверной странице /pay из подписанного токена)
        uid = int(custom.get("uid") or fields.get("AccountId") or 0)
    except (ValueError, TypeError):
        uid = 0
    tx = str(fields.get("TransactionId") or "")
    token = fields.get("Token") or None
    email = fields.get("Email") or None  # для чеков 54-ФЗ при автопродлении
    if not uid or not tariff_code or not tx:
        log.warning("CloudPayments pay: не хватает данных uid=%s tariff=%s tx=%s", uid, tariff_code, tx)
        return JSONResponse({"code": 0})  # подтверждаем приём, активировать нечего
    try:
        result = await services.activate_from_webhook(uid, tariff_code, tx, token=token,
                                                       is_gift=is_gift, email=email)
        await _notify(uid, result)
    except Exception:  # noqa: BLE001
        log.exception("CloudPayments pay: ошибка активации tx=%s", tx)
        # всё равно возвращаем 0 — иначе CP будет слать повтор; idempotency защитит от дубля
    return JSONResponse({"code": 0})


@app.post("/cloudpayments/fail")
async def cp_fail(request: Request):
    fields = await _read_verified(request)
    if fields is None:
        return JSONResponse({"code": 13})
    log.info("CloudPayments fail: tx=%s reason=%s",
             fields.get("TransactionId"), fields.get("Reason"))
    return JSONResponse({"code": 0})


# ------------------------- уведомления в бота -------------------------

async def _notify(uid: int, result: dict) -> None:
    status = result.get("status")
    if status not in ("activated", "gift"):
        return
    bot = _get_bot()
    tariff = result["tariff"]
    try:
        if status == "gift":
            me = await bot.me()
            link = f"https://t.me/{me.username}?start=gift_{result['code']}"
            text = await content.get_text("gift_paid", title=_esc(tariff.title), link=_esc(link))
            await bot.send_message(uid, text, link_preview_options=NO_PREVIEW)
        else:
            sub = result["sub"]
            links = await grant_access(bot, uid)
            thanks = await content.get_text("thanks_text")
            details = await _sub_text(sub, tariff)
            await bot.send_message(uid, f"{thanks}\n\n{details}", reply_markup=kb.subscription_kb(sub, links))
    except Exception:  # noqa: BLE001
        log.exception("Не удалось уведомить пользователя %s об оплате", uid)


async def _sub_text(sub, tariff) -> str:
    if sub.is_forever:
        date = await content.get_setting("forever_date_label")
        autopay = "—"
    else:
        date = services.fmt_msk(sub.expires_at)
        autopay = await content.get_setting("autopay_on" if sub.autorenew else "autopay_off")
    return await content.get_text(
        "my_sub_active", tariff=_esc(tariff.title), emoji=_esc(tariff.emoji),
        date=date, autopay=_esc(autopay),
    )

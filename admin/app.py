"""Веб-админка THE MAIN: смена текстов, цен (тарифов) и базовое администрирование.

Раздаётся по префиксу /admin (настраивается ADMIN_BASE_PATH), поэтому на боевом
сервере адрес: https://<домен>/admin

Запуск (из корня проекта):
    uvicorn admin.app:app --host 127.0.0.1 --port 8000

Требуются переменные окружения (см. .env): BOT_TOKEN, ADMIN_PASSWORD, ADMIN_SECRET,
DATABASE_URL (та же БД, что у бота). Необязательно: ADMIN_BASE_PATH (по умолчанию /admin).
"""
import hmac
import logging
import os
from datetime import datetime

from aiogram import Bot
from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from bot import content, services
from bot.access import allow_rejoin, revoke_access
from bot.config import get_config
from bot.database import Subscription, User, get_sessionmaker, init_db

log = logging.getLogger(__name__)
cfg = get_config()

# Fail-closed: без пароля и стойкого секрета админку не поднимаем.
if not cfg.admin_password:
    raise RuntimeError("ADMIN_PASSWORD не задан — задайте его в .env перед запуском админки.")
if not cfg.admin_secret or len(cfg.admin_secret) < 16:
    raise RuntimeError("ADMIN_SECRET не задан или слишком короткий (нужно ≥16 случайных символов).")

# Префикс, по которому доступна админка (напр. /admin). Пусто = корень домена.
BASE = "/" + os.getenv("ADMIN_BASE_PATH", "admin").strip("/")
if BASE == "/":
    BASE = ""

app = FastAPI(title="THE MAIN — админка")
app.add_middleware(
    SessionMiddleware,
    secret_key=cfg.admin_secret,
    max_age=60 * 60 * 8,
    same_site="strict",
    https_only=cfg.admin_secure_cookies,
)
templates = Jinja2Templates(directory="admin/templates")
router = APIRouter()


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    await content.seed_defaults()


# ------------------------- утилиты -------------------------

def _guard(request: Request) -> RedirectResponse | None:
    if not request.session.get("auth"):
        return RedirectResponse(f"{BASE}/login", status_code=303)
    return None


def _rr(path: str) -> RedirectResponse:
    return RedirectResponse(f"{BASE}{path}", status_code=303)


def _page(request: Request, name: str, ctx: dict) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"base": BASE, **ctx})


async def _active_subs() -> list[Subscription]:
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(select(Subscription).where(Subscription.status == "active"))
        now = datetime.utcnow()
        return [x for x in res.scalars() if x.is_forever or (x.expires_at and x.expires_at > now)]


async def _revoke(tg_id: int) -> None:
    try:
        bot = Bot(cfg.bot_token)
        try:
            await revoke_access(bot, tg_id)
        finally:
            await bot.session.close()
    except Exception:  # noqa: BLE001
        log.exception("admin revoke failed for %s", tg_id)


# ------------------------- аутентификация -------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, e: int = 0):
    return _page(request, "login.html", {"error": bool(e), "no_password": not cfg.admin_password})


@router.post("/login")
async def login(request: Request, password: str = Form("")):
    if cfg.admin_password and hmac.compare_digest(password, cfg.admin_password):
        request.session["auth"] = True
        return _rr("/settings")
    log.warning("admin: неверный пароль при входе")
    return _rr("/login?e=1")


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _rr("/login")


@router.get("/")
async def home(request: Request):
    if (r := _guard(request)):
        return r
    return _rr("/settings")


# ------------------------- тексты / настройки -------------------------

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: int = 0):
    if (r := _guard(request)):
        return r
    values = await content.get_all_settings()
    return _page(request, "settings.html",
                 {"meta": content.SETTINGS_META, "values": values, "saved": bool(saved)})


@router.post("/settings/save")
async def settings_save(request: Request):
    if (r := _guard(request)):
        return r
    form = await request.form()
    for key, *_ in content.SETTINGS_META:
        if key in form:
            await content.set_setting(key, str(form[key]))
    return _rr("/settings?saved=1")


# ------------------------- тарифы (цены) -------------------------

@router.get("/tariffs", response_class=HTMLResponse)
async def tariffs_page(request: Request):
    if (r := _guard(request)):
        return r
    tariffs = await content.get_tariffs(active_only=False)
    return _page(request, "tariffs.html", {"tariffs": tariffs})


@router.post("/tariffs/save")
async def tariffs_save(
    request: Request,
    code: str = Form(...),
    title: str = Form(...),
    emoji: str = Form(""),
    price_rub: int = Form(...),
    months: str = Form(""),
    sort_order: int = Form(0),
    is_active: str = Form(""),
):
    if (r := _guard(request)):
        return r
    months_val = int(months) if months.strip().isdigit() else None
    await content.upsert_tariff(code.strip(), title.strip(), emoji.strip(), price_rub,
                               months_val, sort_order, is_active == "on")
    return _rr("/tariffs")


@router.post("/tariffs/delete")
async def tariffs_delete(request: Request, code: str = Form(...)):
    if (r := _guard(request)):
        return r
    await content.delete_tariff(code)
    return _rr("/tariffs")


# ------------------------- пользователи -------------------------

@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, q: str = ""):
    if (r := _guard(request)):
        return r
    sm = get_sessionmaker()
    q = (q or "").strip()
    async with sm() as s:
        if q.lstrip("-").isdigit():
            stmt = select(User).where(User.tg_id == int(q)).limit(200)
        elif q:
            stmt = select(User).where(User.username.ilike(f"%{q}%")).order_by(User.created_at.desc()).limit(200)
        else:
            stmt = select(User).order_by(User.created_at.desc()).limit(200)
        users = list((await s.execute(stmt)).scalars())

    subs = {x.user_id: x for x in await _active_subs()}
    tariffs = await content.get_tariffs(active_only=False)
    tmap = {t.code: t for t in tariffs}
    rows = []
    for u in users:
        sub = subs.get(u.tg_id)
        if sub:
            t = tmap.get(sub.tariff_id)
            status = "♾️ навсегда" if sub.is_forever else services.fmt_msk(sub.expires_at)
            tariff_title = (t.title if t else sub.tariff_id)
            autopay = ("—" if sub.is_forever else ("вкл" if sub.autorenew else "выкл"))
        else:
            status, tariff_title, autopay = "нет", "—", "—"
        rows.append({
            "tg_id": u.tg_id, "username": u.username or "", "full_name": u.full_name or "",
            "blocked": u.is_blocked, "has_sub": sub is not None,
            "status": status, "tariff": tariff_title, "autopay": autopay,
        })
    return _page(request, "users.html", {"rows": rows, "tariffs": tariffs, "q": q})


@router.post("/users/{tg_id}/grant")
async def user_grant(request: Request, tg_id: int, code: str = Form(...)):
    if (r := _guard(request)):
        return r
    tariff = await content.get_tariff(code)
    if tariff:
        await services.ensure_user(tg_id)
        await services.activate_subscription(tg_id, tariff, autorenew=False,
                                             provider="admin", record_payment=False)
    return _rr("/users")


@router.post("/users/{tg_id}/cancel")
async def user_cancel(request: Request, tg_id: int):
    if (r := _guard(request)):
        return r
    sm = get_sessionmaker()
    async with sm() as s:
        res = await s.execute(select(Subscription).where(Subscription.user_id == tg_id,
                                                         Subscription.status == "active"))
        for sub in res.scalars():
            sub.status = "cancelled"
        await s.commit()
    await _revoke(tg_id)
    return _rr("/users")


@router.post("/users/{tg_id}/stop_autopay")
async def user_stop_autopay(request: Request, tg_id: int):
    if (r := _guard(request)):
        return r
    await services.stop_autorenew(tg_id)
    return _rr("/users")


@router.post("/users/{tg_id}/block")
async def user_block(request: Request, tg_id: int):
    if (r := _guard(request)):
        return r
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, tg_id)
        if u:
            u.is_blocked = True
            await s.commit()
    await services.stop_autorenew(tg_id)
    await _revoke(tg_id)
    return _rr("/users")


@router.post("/users/{tg_id}/unblock")
async def user_unblock(request: Request, tg_id: int):
    if (r := _guard(request)):
        return r
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, tg_id)
        if u:
            u.is_blocked = False
            await s.commit()
    try:
        bot = Bot(cfg.bot_token)
        try:
            await allow_rejoin(bot, tg_id)
        finally:
            await bot.session.close()
    except Exception:  # noqa: BLE001
        log.exception("admin unblock allow_rejoin failed for %s", tg_id)
    return _rr("/users")


app.include_router(router, prefix=BASE)

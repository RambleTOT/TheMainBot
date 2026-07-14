"""Веб-админка THE MAIN: смена текстов, цен (тарифов) и базовое администрирование.

Раздаётся по префиксу /admin (настраивается ADMIN_BASE_PATH), поэтому на боевом
сервере адрес: https://<домен>/admin

Запуск (из корня проекта):
    uvicorn admin.app:app --host 127.0.0.1 --port 8000

Требуются переменные окружения (см. .env): BOT_TOKEN, ADMIN_PASSWORD, ADMIN_SECRET,
DATABASE_URL (та же БД, что у бота). Необязательно: ADMIN_BASE_PATH (по умолчанию /admin).
"""
import asyncio
import hmac
import logging
import os
import time
from datetime import datetime

from aiogram import Bot
from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
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
if len(cfg.admin_password) < 8:
    raise RuntimeError("ADMIN_PASSWORD слишком короткий (нужно ≥8 символов, лучше длиннее и случайный).")
if not cfg.admin_secret or len(cfg.admin_secret) < 16:
    raise RuntimeError("ADMIN_SECRET не задан или слишком короткий (нужно ≥16 случайных символов).")

# Префикс, по которому доступна админка (напр. /admin). Пусто = корень домена.
BASE = "/" + os.getenv("ADMIN_BASE_PATH", "admin").strip("/")
if BASE == "/":
    BASE = ""

# Папка для загруженных картинок (совпадает с MEDIA_DIR у бота).
MEDIA_DIR = os.path.realpath(os.getenv("WELCOME_MEDIA_DIR") or "web/media")
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMG = 5 * 1024 * 1024

# /docs, /redoc, /openapi.json отключены — не раскрываем API-поверхность.
app = FastAPI(title="THE MAIN — админка", docs_url=None, redoc_url=None, openapi_url=None)
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

def _guard(request: Request):
    # необязательный белый список IP (ADMIN_ALLOW_IPS в .env). Пусто = доступ по паролю всем.
    if cfg.admin_allow_ips and _client_ip(request) not in cfg.admin_allow_ips:
        log.warning("admin: доступ с не разрешённого IP %s", _client_ip(request))
        return Response("Доступ запрещён", status_code=403)
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
# Защита от подбора пароля: пер-IP счётчик неудач + блокировка + задержка.
_LOGIN_WINDOW = 600      # окно учёта неудач, сек
_LOGIN_MAX = 8           # неудач до блокировки
_LOGIN_LOCK = 900        # длительность блокировки, сек
_login_fails: dict[str, list[float]] = {}
_login_locked: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    ip = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "?")


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, e: int = 0, locked: int = 0):
    return _page(request, "login.html",
                 {"error": bool(e), "locked": bool(locked), "no_password": not cfg.admin_password})


@router.post("/login")
async def login(request: Request, password: str = Form("")):
    ip = _client_ip(request)
    now = time.monotonic()
    if _login_locked.get(ip, 0) > now:
        return _rr("/login?locked=1")
    # сравниваем байты (compare_digest на str падает, если пароль не ASCII — напр. кириллица)
    if cfg.admin_password and hmac.compare_digest(
        password.encode("utf-8"), cfg.admin_password.encode("utf-8")
    ):
        _login_fails.pop(ip, None)
        _login_locked.pop(ip, None)
        request.session["auth"] = True
        return _rr("/settings")
    # неудача: задержка + учёт
    await asyncio.sleep(0.4)
    fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
    fails.append(now)
    _login_fails[ip] = fails
    if len(fails) >= _LOGIN_MAX:
        _login_locked[ip] = now + _LOGIN_LOCK
        _login_fails.pop(ip, None)
        log.warning("admin: IP %s заблокирован на %d сек после %d неудачных входов", ip, _LOGIN_LOCK, _LOGIN_MAX)
        return _rr("/login?locked=1")
    log.warning("admin: неверный пароль при входе (ip=%s)", ip)
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
async def settings_page(request: Request, saved: int = 0, imgerr: int = 0):
    if (r := _guard(request)):
        return r
    values = await content.get_all_settings()
    images = {k: bool(await content.get_setting(content.image_setting_key(k))) for k in content.IMAGE_MSGS}
    return _page(request, "settings.html",
                 {"meta": content.SETTINGS_META, "values": values, "images": images,
                  "image_msgs": content.IMAGE_MSGS, "saved": bool(saved), "imgerr": bool(imgerr)})


@router.post("/settings/save")
async def settings_save(request: Request):
    if (r := _guard(request)):
        return r
    form = await request.form()
    for key, *_ in content.SETTINGS_META:
        # welcome_image редактируется отдельным блоком загрузки, не из общей формы
        if key != "welcome_image" and key in form:
            await content.set_setting(key, str(form[key]))
    return _rr("/settings?saved=1")


def _img_base(key: str) -> str:
    return os.path.join(MEDIA_DIR, "img_" + key)


@router.post("/settings/upload_image")
async def upload_image(request: Request, key: str = Form(...), file: UploadFile = File(...)):
    if (r := _guard(request)):
        return r
    if key not in content.IMAGE_MSGS:
        return _rr("/settings?imgerr=1")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMG_EXT:
        return _rr("/settings?imgerr=1")
    data = await file.read()
    if not data or len(data) > MAX_IMG:
        return _rr("/settings?imgerr=1")
    dest = _img_base(key) + ext
    try:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
    except OSError:
        log.exception("upload_image: не удалось записать %s (права на web/media?)", dest)
        return _rr("/settings?imgerr=1")
    for e in IMG_EXT - {ext}:  # убрать картинку в другом формате
        try:
            os.remove(_img_base(key) + e)
        except OSError:
            pass
    await content.set_setting(content.image_setting_key(key), dest)
    return _rr("/settings?saved=1")


@router.post("/settings/clear_image")
async def clear_image(request: Request, key: str = Form(...)):
    if (r := _guard(request)):
        return r
    if key not in content.IMAGE_MSGS:
        return _rr("/settings")
    await content.set_setting(content.image_setting_key(key), "")
    for e in IMG_EXT:
        try:
            os.remove(_img_base(key) + e)
        except OSError:
            pass
    return _rr("/settings?saved=1")


@router.get("/media/{key}")
async def media_image(request: Request, key: str):
    if (r := _guard(request)):
        return r
    if key in content.IMAGE_MSGS:
        for e in (".jpg", ".jpeg", ".png", ".webp"):
            p = _img_base(key) + e
            if os.path.isfile(p):
                return FileResponse(p, headers={"Cache-Control": "no-store"})
    return Response(status_code=404)


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

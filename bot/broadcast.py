"""Рассылки из админки: подбор получателей и отправка с учётом лимитов Telegram.

Отправка идёт фоновой задачей в процессе админки. Прогресс (sent/blocked/failed) пишется в
таблицу broadcasts. Если сервис перезапустили посреди рассылки, при старте она помечается
«interrupted» (повторно не рассылается — чтобы никто не получил дубль).
"""
import asyncio
import logging
import os
import secrets
from datetime import datetime

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramAPIError, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.types import FSInputFile
from sqlalchemy import select

from .config import get_config
from .database import Broadcast, Subscription, User, get_sessionmaker

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
SEND_DELAY = 0.05          # ~20 сообщений/с — с запасом под лимит Telegram (30/с)
_tasks: set[asyncio.Task] = set()


def parse_ids(raw: str) -> tuple[list[int], list[str]]:
    """Разбирает список получателей: ID и @username через запятую/пробел/перенос."""
    ids: list[int] = []
    names: list[str] = []
    for part in raw.replace(",", " ").replace(";", " ").split():
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.append(int(part))
        elif part:
            names.append(part.lstrip("@").lower())
    return ids, names


async def recipients(audience: str, audience_list: str = "") -> list[int]:
    """tg_id получателей. Забаненных из админки не трогаем никогда."""
    sm = get_sessionmaker()
    async with sm() as s:
        users = (await s.execute(select(User.tg_id, User.username).where(User.is_blocked.is_(False)))).all()
        if audience == "list":
            ids, names = parse_ids(audience_list)
            idset, nameset = set(ids), set(names)
            return [u.tg_id for u in users
                    if u.tg_id in idset or (u.username and u.username.lower() in nameset)]
        if audience in ("active", "inactive"):
            now = datetime.utcnow()
            subs = (await s.execute(select(Subscription).where(Subscription.status == "active"))).scalars()
            active = {x.user_id for x in subs if x.is_forever or (x.expires_at and x.expires_at > now)}
            want = audience == "active"
            return [u.tg_id for u in users if (u.tg_id in active) == want]
        return [u.tg_id for u in users]


def _split(text: str, media: bool) -> tuple[str | None, str | None]:
    """(caption, отдельный текст). Длинный текст к медиа не влезает в подпись — шлём вторым."""
    text = (text or "").strip()
    if not media:
        return None, text or None
    if len(text) <= CAPTION_LIMIT:
        return text or None, None
    return None, text


async def _send_one(bot: Bot, chat_id: int, b: Broadcast, media_ref, caption, body) -> object | None:
    """Шлёт одному. Возвращает file_id загруженного медиа (чтобы не заливать файл заново)."""
    file_id = None
    if b.media_type:
        method = bot.send_photo if b.media_type == "photo" else bot.send_video
        kw = {"photo" if b.media_type == "photo" else "video": media_ref}
        m = await method(chat_id, caption=caption, **kw)
        file_id = (m.photo[-1].file_id if b.media_type == "photo" else m.video.file_id)
    if body:
        await bot.send_message(chat_id, body)
    return file_id


async def send_test(b: Broadcast) -> int:
    """Тестовая отправка админам из ADMIN_IDS. Возвращает, скольким дошло."""
    cfg = get_config()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    ok = 0
    try:
        caption, body = _split(b.text, bool(b.media_type))
        ref = FSInputFile(b.media_path) if b.media_type else None
        for uid in cfg.admin_ids:
            try:
                await _send_one(bot, uid, b, ref, caption, body)
                ok += 1
            except TelegramAPIError as e:
                log.warning("broadcast test: %s не получил: %s", uid, e)
    finally:
        await bot.session.close()
    return ok


async def _run(broadcast_id: int, ids: list[int]) -> None:
    cfg = get_config()
    sm = get_sessionmaker()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sent = blocked = failed = 0
    try:
        async with sm() as s:
            b = await s.get(Broadcast, broadcast_id)
        caption, body = _split(b.text, bool(b.media_type))
        ref = FSInputFile(b.media_path) if b.media_type else None
        for n, uid in enumerate(ids, 1):
            while True:
                try:
                    fid = await _send_one(bot, uid, b, ref, caption, body)
                    if fid:
                        ref = fid        # дальше шлём по file_id — быстрее и без повторной загрузки
                    sent += 1
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                except TelegramForbiddenError:
                    blocked += 1
                except TelegramAPIError as e:
                    failed += 1
                    log.warning("broadcast %s: user=%s: %s", broadcast_id, uid, e)
                except Exception:  # noqa: BLE001
                    failed += 1
                    log.exception("broadcast %s: user=%s", broadcast_id, uid)
                break
            await asyncio.sleep(SEND_DELAY)
            if n % 20 == 0:
                await _save(broadcast_id, sent=sent, blocked=blocked, failed=failed)
        await _save(broadcast_id, sent=sent, blocked=blocked, failed=failed,
                    status="done", finished_at=datetime.utcnow())
    except Exception:  # noqa: BLE001
        log.exception("broadcast %s: сбой", broadcast_id)
        await _save(broadcast_id, sent=sent, blocked=blocked, failed=failed,
                    status="interrupted", finished_at=datetime.utcnow())
    finally:
        await bot.session.close()


async def _save(broadcast_id: int, **fields) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        b = await s.get(Broadcast, broadcast_id)
        if b:
            for k, v in fields.items():
                setattr(b, k, v)
            await s.commit()


async def is_running() -> bool:
    sm = get_sessionmaker()
    async with sm() as s:
        return (await s.execute(select(Broadcast.id).where(Broadcast.status == "running"))).first() is not None


async def start(text: str, media_path: str | None, media_type: str | None,
                audience: str, audience_list: str) -> int | None:
    """Создаёт рассылку и запускает фоново. None — если уже идёт другая или некому слать."""
    if await is_running():
        return None
    ids = await recipients(audience, audience_list)
    if not ids:
        return None
    sm = get_sessionmaker()
    async with sm() as s:
        b = Broadcast(text=text, media_path=media_path, media_type=media_type, audience=audience,
                      audience_list=audience_list, status="running", total=len(ids))
        s.add(b)
        await s.commit()
        bid = b.id
    task = asyncio.create_task(_run(bid, ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return bid


async def mark_interrupted() -> None:
    """При старте админки: «running» без живой задачи — значит процесс рестартовали."""
    sm = get_sessionmaker()
    async with sm() as s:
        for b in (await s.execute(select(Broadcast).where(Broadcast.status == "running"))).scalars():
            b.status = "interrupted"
            b.finished_at = datetime.utcnow()
        await s.commit()


def new_media_path(ext: str) -> str:
    d = os.path.join(os.path.realpath(os.getenv("WELCOME_MEDIA_DIR") or "web/media"), "broadcasts")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{secrets.token_hex(8)}{ext}")

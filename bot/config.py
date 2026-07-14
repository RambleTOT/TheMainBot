"""Настройки бота. Читаются из .env (см. .env.example)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


def _one_id(raw: str | None) -> int | None:
    ids = _ids(raw)
    return ids[0] if ids else None


def _str_list(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


@dataclass
class Resource:
    key: str           # технический ключ
    title: str         # подпись кнопки
    url: str | None    # публичная ссылка-приглашение
    chat_id: int | None  # ID канала/группы для авто-выдачи и отзыва доступа


@dataclass
class Config:
    bot_token: str
    database_url: str
    admin_ids: list[int]
    test_mode: bool
    resources: list[Resource]
    admin_password: str | None
    admin_secret: str | None
    admin_secure_cookies: bool
    admin_allow_ips: list[str]


def load_config() -> Config:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен бота от @BotFather."
        )
    resources = [
        Resource("privatka", "🔒 Приватка", os.getenv("RES_PRIVATKA_URL") or None, _one_id(os.getenv("RES_PRIVATKA_CHAT_ID"))),
        Resource("forum", "🗣 Форум", os.getenv("RES_FORUM_URL") or None, _one_id(os.getenv("RES_FORUM_CHAT_ID"))),
        Resource("chat", "💬 Чат", os.getenv("RES_CHAT_URL") or None, _one_id(os.getenv("RES_CHAT_CHAT_ID"))),
    ]
    return Config(
        bot_token=token,
        database_url=os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///bot.db",
        admin_ids=_ids(os.getenv("ADMIN_IDS")),
        test_mode=(os.getenv("TEST_MODE", "true").lower() in ("1", "true", "yes")),
        resources=resources,
        admin_password=os.getenv("ADMIN_PASSWORD") or None,
        # Никакого «дефолтного» секрета: без ADMIN_SECRET админку запускать нельзя
        # (иначе cookie сессии можно подделать). Проверяется при старте админки.
        admin_secret=os.getenv("ADMIN_SECRET") or None,
        admin_secure_cookies=(os.getenv("ADMIN_SECURE_COOKIES", "false").lower() in ("1", "true", "yes")),
        admin_allow_ips=_str_list(os.getenv("ADMIN_ALLOW_IPS")),
    )


_config: Config | None = None


def get_config() -> Config:
    """Ленивая инициализация — чтобы импорт модулей не падал без .env."""
    global _config
    if _config is None:
        _config = load_config()
    return _config

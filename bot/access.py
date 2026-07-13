"""Выдача и отзыв доступа к закрытым ресурсам (приватка / форум / чат).

Работает только для ресурсов, у которых в .env указан chat_id и где бот —
администратор с правами «приглашать» и «банить». Для остальных ресурсов
используются публичные ссылки (RES_*_URL).
"""
import logging

from aiogram import Bot

from .config import get_config

log = logging.getLogger(__name__)


async def grant_access(bot: Bot, user_id: int) -> dict[str, str]:
    """Готовит ссылки на ресурсы для пользователя.

    Если у ресурса указан chat_id — создаёт ссылку с ЗАЯВКОЙ НА ВСТУПЛЕНИЕ
    (creates_join_request=True). Заявку одобряет бот и только для оплативших
    (см. on_join_request в handlers.py) — поэтому пересланная ссылка бесполезна.
    Иначе берёт публичную ссылку из настроек. Возвращает {ключ_ресурса: ссылка}.

    Заблокированным пользователям ссылки не выдаём (централизованная защита от
    обхода бана через подарок/активацию, в т.ч. для публичных ссылок без gate).
    """
    from .services import is_blocked
    if await is_blocked(user_id):
        return {}
    cfg = get_config()
    links: dict[str, str] = {}
    for r in cfg.resources:
        if r.chat_id:
            try:
                invite = await bot.create_chat_invite_link(
                    chat_id=r.chat_id,
                    creates_join_request=True,
                    name=f"u{user_id}"[:32],
                )
                links[r.key] = invite.invite_link
                continue
            except Exception as e:  # noqa: BLE001
                log.exception("Не удалось создать invite-ссылку для %s: %s", r.key, e)
        if r.url:
            links[r.key] = r.url
    return links


async def revoke_access(bot: Bot, user_id: int) -> None:
    """Удаляет пользователя из всех настроенных ресурсов по окончании подписки.

    Делается ban + unban: пользователь вылетает из канала/группы, но может
    вернуться позже по новой ссылке (после повторной оплаты).
    """
    cfg = get_config()
    for r in cfg.resources:
        if not r.chat_id:
            continue
        try:
            await bot.ban_chat_member(chat_id=r.chat_id, user_id=user_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Не удалось забанить user=%s resource=%s: %s", user_id, r.key, e)
            continue
        # unban в отдельном try: если он упадёт, пользователь останется забанен —
        # это важно залогировать отдельно, чтобы можно было снять бан вручную (allow_rejoin).
        try:
            await bot.unban_chat_member(chat_id=r.chat_id, user_id=user_id, only_if_banned=True)
            log.info("Доступ отозван: user=%s resource=%s", user_id, r.key)
        except Exception as e:  # noqa: BLE001
            log.exception("ВНИМАНИЕ: unban не прошёл, user=%s остался забанен в resource=%s: %s",
                          user_id, r.key, e)


async def allow_rejoin(bot: Bot, user_id: int) -> None:
    """Снимает бан во всех ресурсах (для разблокировки из админки / восстановления доступа)."""
    cfg = get_config()
    for r in cfg.resources:
        if not r.chat_id:
            continue
        try:
            await bot.unban_chat_member(chat_id=r.chat_id, user_id=user_id, only_if_banned=True)
        except Exception as e:  # noqa: BLE001
            log.exception("allow_rejoin: не удалось снять бан user=%s resource=%s: %s", user_id, r.key, e)

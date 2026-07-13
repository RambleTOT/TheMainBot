"""Задачи по расписанию (APScheduler)."""
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .services import expire_due


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    # Проверка истёкших подписок: автопродление, а при неуспехе — немедленный
    # отзыв доступа (по решению заказчика: без повторных попыток и льготного периода).
    scheduler.add_job(
        expire_due, "interval", minutes=5, args=[bot],
        id="expire_due", max_instances=1, coalesce=True,
    )
    # Напоминания перед списанием ОТКЛЮЧЕНЫ — заказчик попросил не уведомлять заранее.
    return scheduler

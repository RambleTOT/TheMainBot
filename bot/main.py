"""Точка входа. Запуск: python -m bot.main"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import get_config
from .content import seed_defaults
from .database import init_db
from .handlers import router
from .scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")


async def main() -> None:
    cfg = get_config()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await init_db()
    await seed_defaults()
    scheduler = setup_scheduler(bot)
    scheduler.start()
    log.info("Бот запущен (TEST_MODE=%s)", cfg.test_mode)

    try:
        # Первая версия работает на long polling. Для production — webhook + nginx/TLS.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

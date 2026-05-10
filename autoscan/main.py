import asyncio
from loguru import logger
from bot.main import dp, bot
from scheduler.scheduler import start_scheduler, stop_scheduler
from database.connection import init_db


async def main():
    logger.info("🚀 Запуск AutoScan...")

    await init_db()
    logger.info("✅ База данных готова")

    # ВАЖНО:
    # Больше не очищаем объявления при старте.
    # Историческая база и новые объявления должны сохраняться.
    # Раньше тут был вызов cleanup_old_listings().

    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(2)
    logger.info("✅ Webhook сброшен")

    await start_scheduler()
    logger.info("✅ Планировщик запущен")

    logger.info("✅ Telegram бот запущен")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        await stop_scheduler()
        logger.info("AutoScan остановлен")


if __name__ == "__main__":
    asyncio.run(main())
import asyncio
from loguru import logger
from bot.main import dp, bot
from scheduler.scheduler import start_scheduler, stop_scheduler
from database.connection import init_db


async def cleanup_old_listings():
    """Очищаем старые объявления из базы"""
    from database.connection import AsyncSessionLocal
    from database.models import Listing, PriceHistory
    from sqlalchemy import delete, select, func

    async with AsyncSessionLocal() as session:
        count_result = await session.execute(select(func.count(Listing.id)))
        count = count_result.scalar()

        if count > 0:
            # Сначала удаляем историю цен
            await session.execute(delete(PriceHistory))
            # Потом удаляем объявления
            await session.execute(delete(Listing))
            await session.commit()
            logger.info(f"🗑 Очищено {count} старых объявлений")
        else:
            logger.info("База пуста — готовы к работе")


async def main():
    logger.info("🚀 Запуск AutoScan...")

    await init_db()
    logger.info("✅ База данных готова")

    await cleanup_old_listings()

   await bot.delete_webhook(drop_pending_updates=True)
    import asyncio
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

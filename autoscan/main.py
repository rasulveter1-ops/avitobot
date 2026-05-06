import asyncio
from loguru import logger
from bot.main import dp, bot
from scheduler.scheduler import start_scheduler, stop_scheduler
from database.connection import init_db


async def cleanup_old_listings():
    """Очищаем старые объявления из базы чтобы следующий цикл считал их новыми"""
    from database.connection import AsyncSessionLocal
    from database.models import Listing
    from sqlalchemy import delete, select, func
    
    async with AsyncSessionLocal() as session:
        # Считаем сколько объявлений в базе
        count_result = await session.execute(select(func.count(Listing.id)))
        count = count_result.scalar()
        
        if count > 0:
            # Удаляем все объявления кроме тестового
            await session.execute(
                delete(Listing).where(Listing.avito_id != "TEST_001")
            )
            await session.commit()
            logger.info(f"🗑 Очищено {count} старых объявлений из базы")
        else:
            logger.info("База объявлений пуста — готовы к работе")


async def main():
    logger.info("🚀 Запуск AutoScan...")
    
    # Инициализация БД
    await init_db()
    logger.info("✅ База данных готова")
    
    # Очищаем старые объявления при старте
    await cleanup_old_listings()
    
    # Сбрасываем webhook чтобы избежать TelegramConflictError
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Webhook сброшен")
    
    # Запуск планировщика
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

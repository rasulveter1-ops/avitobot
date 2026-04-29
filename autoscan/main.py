import asyncio
from loguru import logger
from bot.main import dp, bot
from scheduler.scheduler import start_scheduler, stop_scheduler
from database.connection import init_db


async def main():
    """Запуск всего сервиса"""
    logger.info("🚀 Запуск AutoScan...")
    
    # Инициализация БД
    await init_db()
    logger.info("✅ База данных готова")
    
    # Запуск планировщика парсинга
    await start_scheduler()
    logger.info("✅ Планировщик запущен")
    
    # Запуск Telegram бота
    logger.info("✅ Telegram бот запущен")
    
    try:
        await dp.start_polling(bot)
    finally:
        await stop_scheduler()
        logger.info("AutoScan остановлен")


if __name__ == "__main__":
    asyncio.run(main())

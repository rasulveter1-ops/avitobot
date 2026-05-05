import asyncio
from loguru import logger
from bot.main import dp, bot
from scheduler.scheduler import start_scheduler, stop_scheduler
from database.connection import init_db


async def send_test_alert():
    """Отправляем тестовый алерт при старте"""
    from database.connection import AsyncSessionLocal
    from database.models import User, Listing
    from sqlalchemy import select
    from bot.main import send_alert
    from datetime import datetime

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).limit(1))
        user = result.scalar_one_or_none()

        if not user:
            logger.info("Пользователь не найден для тестового алерта")
            return

        # Проверяем есть ли уже тестовое объявление
        result = await session.execute(
            select(Listing).where(Listing.avito_id == "TEST_001")
        )
        existing = result.scalar_one_or_none()

        if not existing:
            listing = Listing(
                avito_id="TEST_001",
                url="https://www.avito.ru/moskva/avtomobili/toyota_camry_2019_123456",
                title="Toyota Camry 2.5 AT, 2019",
                price=1200000,
                description="Срочно продаю. Торг уместен. Рассмотрю обмен.",
                brand="Toyota",
                model="Camry",
                year=2019,
                mileage=87000,
                region="Москва",
                photos=[],
                seller_type="private",
                seller_name="Иван",
                market_price=1650000,
                price_diff_pct=-27.3,
                is_urgent=True,
                urgent_keywords=["срочно", "торг уместен", "обмен"],
                is_reseller=False,
                score=8.5,
                analyzed_at=datetime.now(),
                ai_analysis={
                    "score": 8.5,
                    "verdict": "Отличная сделка — цена ниже рынка на 27%",
                    "risks": ["⚠️ Требует осмотра"],
                    "opportunities": ["✅ Срочная продажа — можно торговаться"],
                    "resale_potential": {
                        "estimated_sell_price": 1650000,
                        "estimated_profit": 363000,
                        "profit_probability_pct": 85,
                        "estimated_days_to_sell": 14
                    },
                    "recommendation": "смотреть",
                    "negotiation_tip": "Предложи 1 100 000₽ — скорее всего согласится"
                }
            )
            session.add(listing)
            await session.commit()
            logger.info("Тестовое объявление создано")

            await send_alert(user.telegram_id, listing, listing.ai_analysis)
            logger.info(f"Тестовый алерт отправлен пользователю {user.telegram_id}")


async def main():
    logger.info("🚀 Запуск AutoScan...")
    await init_db()
    logger.info("✅ База данных готова")
    await start_scheduler()
    logger.info("✅ Планировщик запущен")

    # Отправляем тестовый алерт при первом запуске
    await send_test_alert()

    logger.info("✅ Telegram бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await stop_scheduler()
        logger.info("AutoScan остановлен")


if __name__ == "__main__":
    asyncio.run(main())

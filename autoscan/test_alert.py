import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

async def main():
    from database.connection import init_db, AsyncSessionLocal
    from database.models import Listing, User, UserFilter
    from sqlalchemy import select
    from bot.main import send_alert
    from datetime import datetime

    await init_db()

    # Создаём тестовое объявление
    async with AsyncSessionLocal() as session:
        listing = Listing(
            avito_id="TEST_001",
            url="https://www.avito.ru/test",
            title="Toyota Camry 2.5 AT, 2019",
            price=1200000,
            description="Срочно продаю. Торг уместен. Рассмотрю обмен.",
            brand="Toyota",
            model="Camry",
            year=2019,
            mileage=87000,
            region="Москва",
            photos=["https://www.avito.st/s/common/img/avito-logo.svg"],
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

        # Получаем первого пользователя
        result = await session.execute(select(User).limit(1))
        user = result.scalar_one_or_none()

        if user:
            await send_alert(user.telegram_id, listing, listing.ai_analysis)
            print(f"Алерт отправлен пользователю {user.telegram_id}")
        else:
            print("Пользователь не найден — напиши /start боту сначала")

asyncio.run(main())

"""
Пересчёт market_stats с percentile-полями.
Запуск:
    python scripts/rebuild_market_stats.py
"""
import asyncio
from loguru import logger
from sqlalchemy import text

from database.connection import AsyncSessionLocal, init_db


async def rebuild_market_stats():
    await init_db()
    async with AsyncSessionLocal() as session:
        logger.info("Очищаем market_stats...")
        await session.execute(text("TRUNCATE TABLE market_stats RESTART IDENTITY"))

        logger.info("Считаем market_stats с percentile...")
        await session.execute(text("""
            INSERT INTO market_stats (
                brand, model, year, region, city,
                avg_price, min_price, max_price, median_price,
                p10_price, p25_price, p75_price, p90_price,
                listings_count, recorded_at
            )
            SELECT
                brand,
                model,
                year,
                region,
                city,
                AVG(price)::INTEGER AS avg_price,
                MIN(price)::INTEGER AS min_price,
                MAX(price)::INTEGER AS max_price,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY price)::INTEGER AS median_price,
                percentile_cont(0.10) WITHIN GROUP (ORDER BY price)::INTEGER AS p10_price,
                percentile_cont(0.25) WITHIN GROUP (ORDER BY price)::INTEGER AS p25_price,
                percentile_cont(0.75) WITHIN GROUP (ORDER BY price)::INTEGER AS p75_price,
                percentile_cont(0.90) WITHIN GROUP (ORDER BY price)::INTEGER AS p90_price,
                COUNT(*)::INTEGER AS listings_count,
                NOW() AS recorded_at
            FROM listings
            WHERE brand IS NOT NULL
              AND year IS NOT NULL
              AND price IS NOT NULL
              AND price BETWEEN 50000 AND 50000000
              AND is_active = TRUE
            GROUP BY brand, model, year, region, city
            HAVING COUNT(*) >= 5
        """))
        await session.commit()

        result = await session.execute(text("SELECT COUNT(*) FROM market_stats"))
        count = result.scalar() or 0
        logger.info(f"✅ market_stats пересчитан: {count} групп")


if __name__ == "__main__":
    asyncio.run(rebuild_market_stats())

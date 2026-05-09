"""
Market Engine для AutoScan.

Что делает:
1. Считает рыночную статистику по brand + model + year + city/region.
2. Считает price percentile: насколько объявление дешевле рынка.
3. Возвращает понятную категорию сделки: SUPER_DEAL / GOOD_DEAL / MARKET_PRICE / EXPENSIVE.

Важно:
- Percentile считается только по активным объявлениям.
- Для старта используем точное совпадение brand/model/year + город/регион.
- Если аналогов мало, можно расширить год на ±1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


MIN_ANALOGS = 5


@dataclass
class MarketSnapshot:
    analogs_count: int = 0
    min_price: Optional[int] = None
    avg_price: Optional[int] = None
    median_price: Optional[int] = None
    max_price: Optional[int] = None
    p10_price: Optional[int] = None
    p25_price: Optional[int] = None
    p75_price: Optional[int] = None
    p90_price: Optional[int] = None
    price_percentile: Optional[float] = None
    deal_label: str = "NO_DATA"
    price_diff_pct: Optional[float] = None


def classify_deal(price_percentile: Optional[float]) -> str:
    """
    price_percentile = процент объявлений, которые дороже этого авто.

    Пример:
    91 означает: это авто дешевле 91% рынка.
    """
    if price_percentile is None:
        return "NO_DATA"
    if price_percentile >= 90:
        return "SUPER_DEAL"
    if price_percentile >= 75:
        return "GOOD_DEAL"
    if price_percentile >= 40:
        return "MARKET_PRICE"
    if price_percentile >= 20:
        return "ABOVE_MARKET"
    return "EXPENSIVE"


def deal_label_ru(label: str) -> str:
    return {
        "SUPER_DEAL": "🔥 Супер-сделка",
        "GOOD_DEAL": "✅ Хорошая цена",
        "MARKET_PRICE": "⚖️ Рыночная цена",
        "ABOVE_MARKET": "⚠️ Выше рынка",
        "EXPENSIVE": "🧊 Дорого",
        "NO_DATA": "Недостаточно данных",
    }.get(label, label)


async def get_market_snapshot(
    session: AsyncSession,
    *,
    brand: str | None,
    model: str | None,
    year: int | None,
    price: int | None,
    city: str | None = None,
    region: str | None = None,
    mileage: int | None = None,
    year_spread: int = 0,
    mileage_spread_pct: float | None = None,
    exclude_avito_id: str | None = None,
) -> MarketSnapshot:
    """
    Возвращает статистику аналогов и price percentile для конкретного объявления.

    На первом проходе ищем точные аналоги по году.
    Если аналогов меньше MIN_ANALOGS, можно вызвать повторно с year_spread=1.
    """
    if not brand or not year or not price or price <= 0:
        return MarketSnapshot()

    conditions = [
        "brand = :brand",
        "year BETWEEN :year_min AND :year_max",
        "price IS NOT NULL",
        "price BETWEEN 50000 AND 50000000",
        "is_active = TRUE",
    ]
    params = {
        "brand": brand,
        "year_min": year - year_spread,
        "year_max": year + year_spread,
        "price": price,
    }

    if model:
        conditions.append("model = :model")
        params["model"] = model

    # Город точнее региона. Если город есть — считаем по городу.
    if city:
        conditions.append("city = :city")
        params["city"] = city
    elif region:
        conditions.append("region = :region")
        params["region"] = region

    if mileage and mileage_spread_pct:
        low = int(mileage * (1 - mileage_spread_pct))
        high = int(mileage * (1 + mileage_spread_pct))
        conditions.append("mileage BETWEEN :mileage_low AND :mileage_high")
        params["mileage_low"] = low
        params["mileage_high"] = high

    if exclude_avito_id:
        conditions.append("avito_id != :exclude_avito_id")
        params["exclude_avito_id"] = exclude_avito_id

    where_sql = " AND ".join(conditions)

    query = text(f"""
        WITH analogs AS (
            SELECT price
            FROM listings
            WHERE {where_sql}
        ), stats AS (
            SELECT
                COUNT(*)::INTEGER AS analogs_count,
                MIN(price)::INTEGER AS min_price,
                AVG(price)::INTEGER AS avg_price,
                percentile_cont(0.10) WITHIN GROUP (ORDER BY price)::INTEGER AS p10_price,
                percentile_cont(0.25) WITHIN GROUP (ORDER BY price)::INTEGER AS p25_price,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY price)::INTEGER AS median_price,
                percentile_cont(0.75) WITHIN GROUP (ORDER BY price)::INTEGER AS p75_price,
                percentile_cont(0.90) WITHIN GROUP (ORDER BY price)::INTEGER AS p90_price,
                MAX(price)::INTEGER AS max_price
            FROM analogs
        ), cheaper AS (
            SELECT COUNT(*)::INTEGER AS cheaper_or_equal_count
            FROM analogs
            WHERE price >= :price
        )
        SELECT
            stats.*,
            CASE
                WHEN stats.analogs_count > 0
                THEN ROUND((cheaper.cheaper_or_equal_count::NUMERIC / stats.analogs_count::NUMERIC) * 100, 1)
                ELSE NULL
            END AS price_percentile
        FROM stats, cheaper
    """)

    row = (await session.execute(query, params)).mappings().first()
    if not row or not row["analogs_count"]:
        return MarketSnapshot()

    price_percentile = float(row["price_percentile"]) if row["price_percentile"] is not None else None
    median_price = row["median_price"]
    price_diff_pct = None
    if median_price:
        price_diff_pct = round(((price - median_price) / median_price) * 100, 1)

    return MarketSnapshot(
        analogs_count=row["analogs_count"],
        min_price=row["min_price"],
        avg_price=row["avg_price"],
        median_price=median_price,
        max_price=row["max_price"],
        p10_price=row["p10_price"],
        p25_price=row["p25_price"],
        p75_price=row["p75_price"],
        p90_price=row["p90_price"],
        price_percentile=price_percentile,
        deal_label=classify_deal(price_percentile),
        price_diff_pct=price_diff_pct,
    )


async def get_best_market_snapshot(
    session: AsyncSession,
    *,
    brand: str | None,
    model: str | None,
    year: int | None,
    price: int | None,
    city: str | None = None,
    region: str | None = None,
    mileage: int | None = None,
    exclude_avito_id: str | None = None,
) -> MarketSnapshot:
    """
    Последовательность:
    1. Точный год + город.
    2. Год ±1 + город.
    3. Год ±1 без пробега.
    4. Если города нет — регион.
    """
    attempts = [
        {"year_spread": 0, "mileage_spread_pct": 0.30},
        {"year_spread": 1, "mileage_spread_pct": 0.30},
        {"year_spread": 1, "mileage_spread_pct": None},
    ]

    best = MarketSnapshot()
    for attempt in attempts:
        snap = await get_market_snapshot(
            session,
            brand=brand,
            model=model,
            year=year,
            price=price,
            city=city,
            region=region,
            mileage=mileage,
            exclude_avito_id=exclude_avito_id,
            **attempt,
        )
        best = snap
        if snap.analogs_count >= MIN_ANALOGS:
            break
    return best

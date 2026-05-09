import json
import os
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Средние рыночные цены для популярных марок (упрощённая база)
MARKET_PRICES = {
    "Toyota": {"Camry": 1800000, "Corolla": 1200000, "RAV4": 2200000, "Land Cruiser": 5000000},
    "Honda": {"CR-V": 2000000, "Accord": 1600000, "Civic": 1100000},
    "Kia": {"Sorento": 2200000, "Rio": 900000, "Sportage": 1800000},
    "Hyundai": {"Tucson": 1900000, "Solaris": 900000, "Creta": 1500000},
    "BMW": {"3 серия": 2500000, "5 серия": 3500000, "X5": 5000000},
    "Mercedes": {"C-класс": 2800000, "E-класс": 4000000, "GLE": 6000000},
    "Nissan": {"X-Trail": 1800000, "Qashqai": 1600000, "Almera": 800000},
    "Volkswagen": {"Polo": 900000, "Tiguan": 2000000, "Passat": 1500000},
    "Lada": {"Vesta": 700000, "Granta": 600000, "XRAY": 750000},
}

DEFAULT_MARKET_PRICE = 1500000  # дефолтная цена если марки нет в базе


def get_simple_market_price(brand: str, model: str, year: int) -> int:
    """Получение примерной рыночной цены без API"""
    base_price = DEFAULT_MARKET_PRICE

    if brand and brand in MARKET_PRICES:
        brand_prices = MARKET_PRICES[brand]
        if model and model in brand_prices:
            base_price = brand_prices[model]
        else:
            # Берём среднее по марке
            base_price = sum(brand_prices.values()) // len(brand_prices)

    # Корректировка по году
    if year:
        current_year = 2025
        age = current_year - year
        if age <= 2:
            base_price = int(base_price * 1.2)
        elif age <= 5:
            base_price = int(base_price * 1.0)
        elif age <= 10:
            base_price = int(base_price * 0.7)
        elif age <= 15:
            base_price = int(base_price * 0.5)
        else:
            base_price = int(base_price * 0.3)

    return base_price


def score_listing(listing_data: dict, market_price: int) -> dict:
    """
    Простой скоринг без AI — на основе правил
    Возвращает анализ в том же формате что и AI
    """
    score = 5.0  # базовый скор
    risks = []
    opportunities = []

    price = listing_data.get("price", 0)
    year = listing_data.get("year")
    mileage = listing_data.get("mileage")
    seller_type = listing_data.get("seller_type", "private")
    is_urgent = listing_data.get("is_urgent", False)
    urgent_keywords = listing_data.get("urgent_keywords", [])
    has_dealer = listing_data.get("has_dealer_keywords", False)
    photos = listing_data.get("photos", [])
    title = listing_data.get("title", "")
    price_percentile = listing_data.get("price_percentile")
    deal_label = listing_data.get("deal_label")
    market_analogs_count = listing_data.get("market_analogs_count")

    # Price percentile: насколько авто дешевле рынка
    if price_percentile is not None:
        if price_percentile >= 90:
            score += 3.0
            opportunities.append(f"🔥 Дешевле {price_percentile:.0f}% рынка")
        elif price_percentile >= 75:
            score += 2.0
            opportunities.append(f"✅ Дешевле {price_percentile:.0f}% рынка")
        elif price_percentile <= 20:
            score -= 2.0
            risks.append(f"⚠️ Дороже большинства аналогов: дешевле только {price_percentile:.0f}% рынка")

    # Сравнение с рыночной ценой
    price_diff_pct = 0
    if market_price and price:
        price_diff_pct = ((price - market_price) / market_price) * 100

        if price_diff_pct <= -20:
            score += 3.0
            opportunities.append(f"✅ Цена ниже рынка на {abs(price_diff_pct):.0f}%")
        elif price_diff_pct <= -10:
            score += 2.0
            opportunities.append(f"✅ Цена ниже рынка на {abs(price_diff_pct):.0f}%")
        elif price_diff_pct <= -5:
            score += 1.0
            opportunities.append(f"✅ Цена немного ниже рынка")
        elif price_diff_pct >= 20:
            score -= 2.0
            risks.append(f"⚠️ Цена выше рынка на {price_diff_pct:.0f}%")
        elif price_diff_pct >= 10:
            score -= 1.0
            risks.append(f"⚠️ Цена немного выше рынка")

    # Тип продавца
    if seller_type == "private" and not has_dealer:
        score += 1.5
        opportunities.append("✅ Частник — можно торговаться")
    elif has_dealer or seller_type == "dealer":
        score -= 1.0
        risks.append("⚠️ Дилер — цена с наценкой")

    # Срочность
    if is_urgent:
        score += 1.5
        opportunities.append(f"🚨 Срочная продажа: {', '.join(urgent_keywords[:2])}")

    # Пробег
    if mileage:
        if mileage <= 50000:
            score += 1.0
            opportunities.append(f"✅ Небольшой пробег: {mileage:,} км".replace(",", " "))
        elif mileage <= 100000:
            score += 0.5
        elif mileage >= 200000:
            score -= 1.0
            risks.append(f"⚠️ Большой пробег: {mileage:,} км".replace(",", " "))

    # Год
    if year:
        if year >= 2020:
            score += 1.0
        elif year >= 2015:
            score += 0.5
        elif year < 2010:
            score -= 0.5
            risks.append(f"⚠️ Старый автомобиль: {year} год")

    # Фото
    if photos:
        score += 0.5
    else:
        score -= 0.5
        risks.append("⚠️ Нет фотографий")

    # Ограничиваем скор
    score = max(1.0, min(10.0, round(score, 1)))

    # Потенциал перепродажи
    estimated_profit = 0
    profit_pct = 0
    days_to_sell = 30

    if market_price and price:
        potential_sell = int(market_price * 0.95)
        estimated_profit = potential_sell - price - 30000  # вычитаем расходы
        if estimated_profit > 0:
            profit_pct = min(85, max(20, int((estimated_profit / price) * 100 * 5)))
            days_to_sell = 21 if score >= 7 else 45

    # Рекомендация
    if score >= 7:
        recommendation = "смотреть"
        verdict = f"Хорошая сделка — оценка {score}/10"
    elif score >= 5:
        recommendation = "торговаться"
        verdict = f"Средняя сделка — стоит осмотреть"
    else:
        recommendation = "пропустить"
        verdict = f"Невыгодная сделка"

    # Совет по торгу
    if price and market_price:
        target = int(price * 0.9)
        negotiation_tip = f"Предложи {target:,}₽ — это -10% от цены продавца".replace(",", " ")
    else:
        negotiation_tip = "Узнай реальную рыночную цену перед торгом"

    return {
        "score": score,
        "verdict": verdict,
        "price_analysis": {
            "is_below_market": price_diff_pct < 0,
            "diff_percent": round(price_diff_pct, 1),
            "price_percentile": price_percentile,
            "deal_label": deal_label,
            "market_analogs_count": market_analogs_count,
            "comment": f"Рыночная цена ~{market_price:,}₽".replace(",", " ")
        },
        "urgency": {
            "is_urgent": is_urgent,
            "level": "high" if is_urgent else "none",
            "reason": ", ".join(urgent_keywords) if urgent_keywords else "Нет признаков срочности"
        },
        "seller_analysis": {
            "is_reseller": has_dealer or seller_type == "dealer",
            "trust_level": "high" if seller_type == "private" else "low",
            "comment": "Частное лицо" if seller_type == "private" else "Компания или дилер"
        },
        "risks": risks,
        "opportunities": opportunities,
        "resale_potential": {
            "estimated_sell_price": int(market_price * 0.95) if market_price else 0,
            "estimated_profit": max(0, estimated_profit),
            "profit_probability_pct": profit_pct,
            "estimated_days_to_sell": days_to_sell
        },
        "recommendation": recommendation,
        "negotiation_tip": negotiation_tip
    }


async def analyze_listing(listing_data: dict, market_price: int = None) -> dict:
    """Анализ объявления — без AI, на правилах"""
    logger.info(f"Анализируем объявление {listing_data.get('avito_id')}")

    if not market_price:
        market_price = get_simple_market_price(
            listing_data.get("brand", ""),
            listing_data.get("model", ""),
            listing_data.get("year")
        )

    result = score_listing(listing_data, market_price)
    logger.info(f"Скор: {result['score']} | {result['verdict']}")
    return result


async def get_market_price(brand: str, model: str, year: int, mileage: int, region: str) -> int:
    """Получение рыночной цены — без API"""
    return get_simple_market_price(brand, model, year)


async def ask_advisor(question: str, context: dict = None) -> str:
    """AI-советник — временно отключён"""
    return (
        "AI-советник временно недоступен — идёт пополнение баланса API.\n\n"
        "Вы можете:\n"
        "• Посмотреть сохранённые объявления\n"
        "• Настроить фильтры\n"
        "• Проверить аналитику рынка"
    )

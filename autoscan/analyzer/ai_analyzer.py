import anthropic
import base64
import httpx
import json
import os
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Промпт для анализа объявления
ANALYSIS_PROMPT = """Ты эксперт по покупке и перепродаже автомобилей в России с 15-летним опытом.
Проанализируй объявление о продаже автомобиля и дай детальную оценку.

ДАННЫЕ ОБЪЯВЛЕНИЯ:
Заголовок: {title}
Цена: {price} руб.
Рыночная цена аналогов: {market_price} руб.
Год: {year}
Пробег: {mileage} км
Продавец: {seller_type}
Количество объявлений у продавца: {seller_listings_count}
Регион: {region}
Описание: {description}
Ключевые слова: {keywords}

Дай анализ в формате JSON:
{{
    "score": <число от 1 до 10, общая оценка выгодности>,
    "verdict": "<одна строка — главный вывод>",
    "price_analysis": {{
        "is_below_market": <true/false>,
        "diff_percent": <% отклонения от рынка, отрицательное = дешевле>,
        "comment": "<комментарий по цене>"
    }},
    "urgency": {{
        "is_urgent": <true/false>,
        "level": "<none/low/medium/high>",
        "reason": "<почему срочно или нет>"
    }},
    "seller_analysis": {{
        "is_reseller": <true/false>,
        "trust_level": "<low/medium/high>",
        "comment": "<анализ продавца>"
    }},
    "risks": [
        "<риск 1>",
        "<риск 2>"
    ],
    "opportunities": [
        "<возможность 1>",
        "<возможность 2>"
    ],
    "resale_potential": {{
        "estimated_sell_price": <примерная цена продажи>,
        "estimated_profit": <примерная прибыль>,
        "profit_probability_pct": <вероятность успешной перепродажи 0-100>,
        "estimated_days_to_sell": <примерное время продажи в днях>
    }},
    "recommendation": "<смотреть/торговаться/пропустить>",
    "negotiation_tip": "<совет по торгу>"
}}

Отвечай ТОЛЬКО валидным JSON без лишнего текста."""

PHOTO_ANALYSIS_PROMPT = """Ты эксперт по диагностике автомобилей.
Внимательно изучи фотографии автомобиля и найди:

1. Видимые повреждения кузова (вмятины, царапины, сколы)
2. Признаки перекраса (разные оттенки панелей, следы шпаклёвки)
3. Следы коррозии или ржавчины
4. Несоответствия (панели не подходят друг другу по зазорам)
5. Признаки стоковых/нереальных фото (студийный фон, водяные знаки)
6. Общее состояние салона если видно
7. Состояние шин и дисков если видно

Отвечай в формате JSON:
{{
    "overall_condition": "<отличное/хорошее/среднее/плохое>",
    "is_stock_photo": <true если фото явно не реальные>,
    "body_issues": [
        "<проблема 1>",
        "<проблема 2>"
    ],
    "repaint_signs": <true/false>,
    "repaint_details": "<где именно признаки перекраса если есть>",
    "rust_signs": <true/false>,
    "photo_quality": "<хорошее/плохое/скрывает детали>",
    "red_flags": [
        "<красный флаг 1>"
    ],
    "comment": "<общий вывод по фото>"
}}

Отвечай ТОЛЬКО валидным JSON."""


async def analyze_listing(listing_data: dict, market_price: int = None) -> dict:
    """
    Полный AI-анализ объявления: текст + фото
    Возвращает объединённый анализ
    """
    logger.info(f"Анализируем объявление {listing_data.get('avito_id')}")
    
    # 1. Анализ текста
    text_analysis = await _analyze_text(listing_data, market_price)
    
    # 2. Анализ фото (если есть)
    photo_analysis = None
    photos = listing_data.get("photos", [])
    if photos:
        photo_analysis = await _analyze_photos(photos[:4])  # максимум 4 фото
    
    # 3. Объединяем результаты
    result = _merge_analysis(text_analysis, photo_analysis)
    
    return result


async def _analyze_text(listing_data: dict, market_price: int = None) -> dict:
    """Анализ текста объявления через Claude"""
    try:
        prompt = ANALYSIS_PROMPT.format(
            title=listing_data.get("title", ""),
            price=listing_data.get("price", 0),
            market_price=market_price or "неизвестна",
            year=listing_data.get("year", "неизвестен"),
            mileage=listing_data.get("mileage", "неизвестен"),
            seller_type="Частник" if listing_data.get("seller_type") == "private" else "Дилер/Компания",
            seller_listings_count=listing_data.get("seller_listings_count", "неизвестно"),
            region=listing_data.get("region", "неизвестен"),
            description=listing_data.get("description", "")[:1000],
            keywords=", ".join(listing_data.get("urgent_keywords", []))
        )
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        raw = response.content[0].text.strip()
        # Очищаем от markdown если вдруг есть
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
        
    except Exception as e:
        logger.error(f"Ошибка текстового анализа: {e}")
        return _default_text_analysis()


async def _analyze_photos(photo_urls: list) -> dict:
    """Анализ фотографий через Claude Vision"""
    try:
        # Загружаем фото и конвертируем в base64
        images_content = []
        
        async with httpx.AsyncClient(timeout=30) as http_client:
            for url in photo_urls[:4]:
                try:
                    resp = await http_client.get(url)
                    if resp.status_code == 200:
                        img_data = base64.standard_b64encode(resp.content).decode("utf-8")
                        # Определяем тип изображения
                        content_type = resp.headers.get("content-type", "image/jpeg")
                        if "png" in content_type:
                            media_type = "image/png"
                        elif "webp" in content_type:
                            media_type = "image/webp"
                        else:
                            media_type = "image/jpeg"
                        
                        images_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_data
                            }
                        })
                except Exception as e:
                    logger.warning(f"Не удалось загрузить фото {url}: {e}")
                    continue
        
        if not images_content:
            return _default_photo_analysis()
        
        # Добавляем текстовый промпт
        images_content.append({
            "type": "text",
            "text": PHOTO_ANALYSIS_PROMPT
        })
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": images_content}]
        )
        
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
        
    except Exception as e:
        logger.error(f"Ошибка анализа фото: {e}")
        return _default_photo_analysis()


def _merge_analysis(text_analysis: dict, photo_analysis: dict = None) -> dict:
    """Объединение текстового и визуального анализа"""
    result = text_analysis.copy()
    
    if photo_analysis:
        result["photo_analysis"] = photo_analysis
        
        # Корректируем скоринг с учётом фото
        score = result.get("score", 5.0)
        
        if photo_analysis.get("is_stock_photo"):
            score -= 2.0
            result.setdefault("risks", []).append("⚠️ Возможно стоковые фото — машины может не существовать")
        
        if photo_analysis.get("repaint_signs"):
            score -= 1.5
            result.setdefault("risks", []).append(
                f"🔴 Признаки перекраса: {photo_analysis.get('repaint_details', '')}"
            )
        
        if photo_analysis.get("rust_signs"):
            score -= 1.0
            result.setdefault("risks", []).append("🔴 Следы ржавчины на фото")
        
        body_issues = photo_analysis.get("body_issues", [])
        if body_issues:
            score -= 0.5 * len(body_issues)
            for issue in body_issues:
                result.setdefault("risks", []).append(f"⚠️ {issue}")
        
        # Ограничиваем скоринг
        result["score"] = max(1.0, min(10.0, round(score, 1)))
    
    return result


async def get_market_price(brand: str, model: str, year: int, mileage: int, region: str) -> int:
    """
    Получение рыночной цены через Claude на основе данных в базе
    В будущем — из накопленной статистики MarketStats
    """
    try:
        prompt = f"""Какова средняя рыночная цена на {brand} {model} {year} года 
        с пробегом около {mileage} км в регионе {region} в 2025 году?
        
        Отвечай ТОЛЬКО числом — ценой в рублях без пробелов и символов. 
        Например: 1500000"""
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        
        price_text = response.content[0].text.strip()
        price = int(''.join(filter(str.isdigit, price_text)))
        return price
        
    except Exception as e:
        logger.error(f"Ошибка получения рыночной цены: {e}")
        return 0


async def ask_advisor(question: str, context: dict = None) -> str:
    """
    AI-советник — отвечает на вопросы пользователя об авторынке
    """
    system = """Ты опытный эксперт по покупке и перепродаже автомобилей в России.
    Отвечаешь кратко, конкретно и по делу. Даёшь практичные советы.
    Используешь актуальные знания о российском авторынке."""
    
    messages = []
    
    if context:
        messages.append({
            "role": "user",
            "content": f"Контекст: {json.dumps(context, ensure_ascii=False)}\n\nВопрос: {question}"
        })
    else:
        messages.append({"role": "user", "content": question})
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Ошибка AI-советника: {e}")
        return "Не удалось получить ответ. Попробуйте позже."


def _default_text_analysis() -> dict:
    """Дефолтный анализ если Claude недоступен"""
    return {
        "score": 5.0,
        "verdict": "Анализ недоступен",
        "price_analysis": {"is_below_market": False, "diff_percent": 0, "comment": ""},
        "urgency": {"is_urgent": False, "level": "none", "reason": ""},
        "seller_analysis": {"is_reseller": False, "trust_level": "medium", "comment": ""},
        "risks": [],
        "opportunities": [],
        "resale_potential": {
            "estimated_sell_price": 0,
            "estimated_profit": 0,
            "profit_probability_pct": 0,
            "estimated_days_to_sell": 0
        },
        "recommendation": "пропустить",
        "negotiation_tip": ""
    }


def _default_photo_analysis() -> dict:
    """Дефолтный анализ фото"""
    return {
        "overall_condition": "неизвестно",
        "is_stock_photo": False,
        "body_issues": [],
        "repaint_signs": False,
        "repaint_details": "",
        "rust_signs": False,
        "photo_quality": "неизвестно",
        "red_flags": [],
        "comment": "Фото не удалось проанализировать"
    }

import anthropic
import base64
import httpx
import json
import os
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

ANALYSIS_PROMPT = """Ты эксперт по покупке и перепродаже автомобилей в России с 15-летним опытом.
Проанализируй объявление о продаже автомобиля и дай детальную оценку.

ДАННЫЕ ОБЪЯВЛЕНИЯ:
Заголовок: {title}
Цена: {price} руб.
Рыночная цена аналогов: {market_price} руб.
Год: {year}
Пробег: {mileage} км
Продавец: {seller_type}
Регион: {region}
Описание: {description}
Ключевые слова: {keywords}

Дай анализ в формате JSON. Используй только двойные кавычки. Не используй переносы строк внутри строковых значений:
{{"score": 7, "verdict": "Текст вывода", "price_analysis": {{"is_below_market": true, "diff_percent": -15, "comment": "Текст"}}, "urgency": {{"is_urgent": false, "level": "none", "reason": "Текст"}}, "seller_analysis": {{"is_reseller": false, "trust_level": "medium", "comment": "Текст"}}, "risks": ["риск 1", "риск 2"], "opportunities": ["возможность 1"], "resale_potential": {{"estimated_sell_price": 1500000, "estimated_profit": 200000, "profit_probability_pct": 70, "estimated_days_to_sell": 21}}, "recommendation": "смотреть", "negotiation_tip": "Текст совета"}}

Отвечай ТОЛЬКО валидным JSON без лишнего текста и без переносов строк внутри значений."""

PHOTO_ANALYSIS_PROMPT = """Ты эксперт по диагностике автомобилей.
Изучи фотографии и найди дефекты.

Отвечай ТОЛЬКО валидным JSON без переносов строк внутри значений:
{{"overall_condition": "хорошее", "is_stock_photo": false, "body_issues": ["проблема 1"], "repaint_signs": false, "repaint_details": "нет", "rust_signs": false, "photo_quality": "хорошее", "red_flags": [], "comment": "Текст вывода"}}"""


def _parse_json_safe(text: str) -> dict:
    """Безопасный парсинг JSON с очисткой"""
    text = text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    # Находим JSON между первой { и последней }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    try:
        return json.loads(text)
    except Exception:
        # Пробуем починить — убираем управляющие символы
        import re
        text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        try:
            return json.loads(text)
        except Exception:
            return {}


async def analyze_listing(listing_data: dict, market_price: int = None) -> dict:
    logger.info(f"Анализируем объявление {listing_data.get('avito_id')}")
    text_analysis = await _analyze_text(listing_data, market_price)
    photo_analysis = None
    photos = listing_data.get("photos", [])
    if photos:
        photo_analysis = await _analyze_photos(photos[:4])
    result = _merge_analysis(text_analysis, photo_analysis)
    return result


async def _analyze_text(listing_data: dict, market_price: int = None) -> dict:
    try:
        prompt = ANALYSIS_PROMPT.format(
            title=listing_data.get("title", ""),
            price=listing_data.get("price", 0),
            market_price=market_price or "неизвестна",
            year=listing_data.get("year", "неизвестен"),
            mileage=listing_data.get("mileage", "неизвестен"),
            seller_type="Частник" if listing_data.get("seller_type") == "private" else "Дилер",
            region=listing_data.get("location", "неизвестен"),
            description=str(listing_data.get("description", ""))[:500],
            keywords=", ".join(listing_data.get("urgent_keywords", []))
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text
        result = _parse_json_safe(raw)
        if result:
            return result
        logger.warning("JSON не распарсился, используем дефолт")
        return _default_text_analysis()

    except Exception as e:
        logger.error(f"Ошибка текстового анализа: {e}")
        return _default_text_analysis()


async def _analyze_photos(photo_urls: list) -> dict:
    try:
        images_content = []

        async with httpx.AsyncClient(timeout=30) as http_client:
            for url in photo_urls[:4]:
                try:
                    resp = await http_client.get(url)
                    if resp.status_code == 200:
                        img_data = base64.standard_b64encode(resp.content).decode("utf-8")
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

        images_content.append({
            "type": "text",
            "text": PHOTO_ANALYSIS_PROMPT
        })

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": images_content}]
        )

        raw = response.content[0].text
        result = _parse_json_safe(raw)
        if result:
            return result
        return _default_photo_analysis()

    except Exception as e:
        logger.error(f"Ошибка анализа фото: {e}")
        return _default_photo_analysis()


def _merge_analysis(text_analysis: dict, photo_analysis: dict = None) -> dict:
    result = text_analysis.copy()

    if photo_analysis:
        result["photo_analysis"] = photo_analysis
        score = result.get("score", 5.0)

        if photo_analysis.get("is_stock_photo"):
            score -= 2.0
            result.setdefault("risks", []).append("⚠️ Возможно стоковые фото")

        if photo_analysis.get("repaint_signs"):
            score -= 1.5
            result.setdefault("risks", []).append(
                f"🔴 Признаки перекраса: {photo_analysis.get('repaint_details', '')}"
            )

        if photo_analysis.get("rust_signs"):
            score -= 1.0
            result.setdefault("risks", []).append("🔴 Следы ржавчины на фото")

        for issue in photo_analysis.get("body_issues", []):
            score -= 0.5
            result.setdefault("risks", []).append(f"⚠️ {issue}")

        result["score"] = max(1.0, min(10.0, round(score, 1)))

    return result


async def get_market_price(brand: str, model: str, year: int, mileage: int, region: str) -> int:
    try:
        prompt = (
            f"Средняя рыночная цена на {brand} {model} {year} года "
            f"с пробегом {mileage} км в регионе {region} в 2025 году? "
            f"Отвечай ТОЛЬКО числом без пробелов. Например: 1500000"
        )

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )

        price_text = response.content[0].text.strip()
        price = int(''.join(filter(str.isdigit, price_text)))
        return price

    except Exception as e:
        logger.error(f"Ошибка рыночной цены: {e}")
        return 0


async def ask_advisor(question: str, context: dict = None) -> str:
    system = """Ты опытный эксперт по покупке и перепродаже автомобилей в России.
    Отвечаешь кратко, конкретно и по делу. Даёшь практичные советы."""

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
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Ошибка AI-советника: {e}")
        return "Не удалось получить ответ. Попробуйте позже."


def _default_text_analysis() -> dict:
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

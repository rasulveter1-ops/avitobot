import asyncio
import re
import os
from datetime import datetime, timedelta
from typing import Optional
import httpx
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

RESTAPP_LOGIN = os.getenv("RESTAPP_LOGIN")
RESTAPP_TOKEN = os.getenv("RESTAPP_TOKEN")
RESTAPP_BASE = "https://rest-app.net/api"

# category_id=9 — Авто на Авито
AVITO_AUTO_CATEGORY = 9

URGENT_KEYWORDS = [
    "срочно", "уезжаю", "переезд", "нужны деньги",
    "вынужден продать", "торг уместен", "торг при осмотре",
    "рассмотрю предложения", "обмен", "рассмотрю обмен",
    "быстро продам", "срочная продажа"
]

DEALER_KEYWORDS = [
    "автосалон", "официальный дилер", "трейд-ин", "лизинг", "салон"
]


class AvitoParser:
    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None
        self.last_check_time: Optional[datetime] = None

    async def start(self):
        self.client = httpx.AsyncClient(timeout=30)
        self.last_check_time = datetime.now() - timedelta(minutes=35)
        logger.info("Парсер запущен (rest-app.net API)")

    async def stop(self):
        if self.client:
            await self.client.aclose()
        logger.info("Парсер остановлен")

    async def parse_search_page(
        self,
        brand: str = None,
        model: str = None,
        price_min: int = None,
        price_max: int = None,
        year_min: int = None,
        year_max: int = None,
        region: str = "moskva",
        radius: int = None,
        page: int = 1
    ) -> list[dict]:
        """
        Получение объявлений через rest-app.net API
        Возвращает объявления за последние 35 минут
        """
        if not RESTAPP_LOGIN or not RESTAPP_TOKEN:
            logger.error("RESTAPP_LOGIN или RESTAPP_TOKEN не заданы")
            return []

        # Время для фильтрации — берём с небольшим запасом
        date2 = datetime.now()
        date1 = self.last_check_time or (date2 - timedelta(minutes=35))

        params = {
            "login": RESTAPP_LOGIN,
            "token": RESTAPP_TOKEN,
            "category_id": AVITO_AUTO_CATEGORY,
            "date1": date1.strftime("%Y-%m-%d %H:%M:%S"),
            "date2": date2.strftime("%Y-%m-%d %H:%M:%S"),
            "limit": 50,  # тестовый лимит 50
        }

        # Добавляем фильтры если заданы
        if price_min:
            params["price_min"] = price_min
        if price_max:
            params["price_max"] = price_max
        if region and region != "rossiya":
            city_name = self._slug_to_city(region)
            if city_name:
                params["city"] = city_name

        url = f"{RESTAPP_BASE}/ads"
        logger.info(f"Запрос к rest-app.net: {date1} — {date2}, регион={region}, марка={brand}")

        try:
            resp = await self.client.get(url, params=params)

            if resp.status_code != 200:
                logger.warning(f"Статус {resp.status_code}: {resp.text[:200]}")
                return []

            data = resp.json()

            if data.get("status") == "error":
                logger.error(f"Ошибка API: {data.get('message')}")
                return []

            ads = data.get("data", [])
            logger.info(f"Получено объявлений: {len(ads)}")

            # Обновляем время последней проверки
            self.last_check_time = date2

            listings = []
            for ad in ads:
                listing = self._extract_listing(ad, brand, model)
                if listing:
                    listings.append(listing)

            logger.info(f"Обработано: {len(listings)}")
            return listings

        except Exception as e:
            logger.error(f"Ошибка запроса к API: {type(e).__name__}: {e}")
            return []

    def _extract_listing(self, ad: dict, filter_brand: str = None, filter_model: str = None) -> Optional[dict]:
        """Извлечение данных из объекта объявления"""
        try:
            avito_id = str(ad.get("id", ""))
            if not avito_id:
                return None

            title = ad.get("title", "")
            price = ad.get("price", 0)
            try:
                price = int(str(price).replace(" ", "").replace("₽", ""))
            except:
                price = 0

            url = ad.get("url", "") or ad.get("link", "")
            description = ad.get("description", "") or ""
            city = ad.get("city", "") or ad.get("region", "")
            date_str = ad.get("date", "") or ad.get("date_add", "")

            # Фото
            photos = []
            photo_fields = ["photo", "photos", "images", "image"]
            for field in photo_fields:
                val = ad.get(field)
                if isinstance(val, list):
                    photos = [p for p in val if isinstance(p, str) and p.startswith("http")]
                    break
                elif isinstance(val, str) and val.startswith("http"):
                    photos = [val]
                    break

            # Параметры авто
            params = ad.get("params", {}) or {}
            year = None
            mileage = None

            # Пробуем разные поля для года и пробега
            year_raw = params.get("Год выпуска") or ad.get("year") or params.get("year")
            if year_raw:
                try:
                    year = int(str(year_raw))
                except:
                    year_match = re.search(r"\b(19|20)\d{2}\b", str(year_raw))
                    if year_match:
                        year = int(year_match.group())

            mileage_raw = params.get("Пробег") or ad.get("mileage") or params.get("km")
            if mileage_raw:
                mileage_clean = re.sub(r"[^\d]", "", str(mileage_raw))
                if mileage_clean:
                    mileage = int(mileage_clean)

            # Если год/пробег в заголовке
            if not year:
                year_match = re.search(r"\b(19|20)\d{2}\b", title)
                if year_match:
                    year = int(year_match.group())

            # Марка и модель
            brand = ad.get("brand") or ad.get("mark")
            model = ad.get("model")
            if not brand:
                brand, model = self.parse_brand_model_from_title(title)

            # Фильтрация по марке если задана
            if filter_brand and brand:
                if filter_brand.lower() not in brand.lower():
                    return None
            if filter_model and model:
                if filter_model.lower() not in model.lower():
                    return None

            # Продавец
            seller_name = ad.get("name") or ad.get("seller") or ""
            seller_type = "private"
            seller_count = 1

            # Ключевые слова
            full_text = f"{title} {description}".lower()
            urgent_keywords = [kw for kw in URGENT_KEYWORDS if kw in full_text]
            is_urgent = len(urgent_keywords) > 0
            has_dealer = any(kw in full_text for kw in DEALER_KEYWORDS)

            if has_dealer:
                seller_type = "dealer"

            return {
                "avito_id": avito_id,
                "url": url,
                "title": title,
                "price": price,
                "description": description,
                "year": year,
                "mileage": mileage,
                "location": city,
                "photos": photos[:10],
                "seller_name": seller_name,
                "seller_type": seller_type,
                "seller_listings_count": seller_count,
                "brand": brand,
                "model": model,
                "urgent_keywords": urgent_keywords,
                "is_urgent": is_urgent,
                "has_dealer_keywords": has_dealer,
            }

        except Exception as e:
            logger.error(f"Ошибка извлечения: {e}")
            return None

    async def parse_listing_detail(self, url: str) -> Optional[dict]:
        """Получение деталей объявления по ID"""
        if not url:
            return None

        # Извлекаем ID из URL
        id_match = re.search(r"_(\d+)$", url.rstrip("/"))
        if not id_match:
            return None

        ad_id = id_match.group(1)

        try:
            params = {
                "login": RESTAPP_LOGIN,
                "token": RESTAPP_TOKEN,
                "id": ad_id,
            }
            resp = await self.client.get(f"{RESTAPP_BASE}/ad", params=params)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") != "error":
                    ad = data.get("data", {})
                    return {
                        "description": ad.get("description", ""),
                        "photos": ad.get("photos", []),
                    }
        except Exception as e:
            logger.error(f"Ошибка деталей {url}: {e}")

        return None

    def _slug_to_city(self, slug: str) -> Optional[str]:
        mapping = {
            "moskva": "Москва",
            "sankt-peterburg": "Санкт-Петербург",
            "kazan": "Казань",
            "novosibirsk": "Новосибирск",
            "ekaterinburg": "Екатеринбург",
            "krasnodar": "Краснодар",
            "samara": "Самара",
            "rostov-na-donu": "Ростов-на-Дону",
            "nizhniy_novgorod": "Нижний Новгород",
            "chelyabinsk": "Челябинск",
            "ufa": "Уфа",
            "voronezh": "Воронеж",
            "perm": "Пермь",
            "omsk": "Омск",
            "volgograd": "Волгоград",
        }
        return mapping.get(slug)

    def parse_brand_model_from_title(self, title: str) -> tuple:
        brands = [
            "Toyota", "Honda", "Kia", "Hyundai", "Nissan", "Mazda",
            "BMW", "Mercedes", "Audi", "Volkswagen", "Skoda", "Lada",
            "Renault", "Ford", "Chevrolet", "Lexus", "Infiniti",
            "Subaru", "Mitsubishi", "Suzuki", "Volvo", "Jeep",
            "Porsche", "Land Rover", "Jaguar", "Chery", "Geely",
            "Haval", "Exeed", "Omoda", "Kaiyi"
        ]
        title_lower = title.lower()
        for brand in brands:
            if brand.lower() in title_lower:
                idx = title_lower.index(brand.lower()) + len(brand)
                rest = title[idx:].strip().split()
                model = rest[0] if rest else None
                return brand, model
        return None, None

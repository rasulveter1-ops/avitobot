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
        # Берём объявления за последние 35 минут
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
        if not RESTAPP_LOGIN or not RESTAPP_TOKEN:
            logger.error("RESTAPP_LOGIN или RESTAPP_TOKEN не заданы")
            return []

        date2 = datetime.now()
        date1 = self.last_check_time or (date2 - timedelta(minutes=35))

        params = {
            "login": RESTAPP_LOGIN,
            "token": RESTAPP_TOKEN,
            "category_id": AVITO_AUTO_CATEGORY,
            "date1": date1.strftime("%Y-%m-%d %H:%M:%S"),
            "date2": date2.strftime("%Y-%m-%d %H:%M:%S"),
            "limit": 1000,
        }

        if price_min:
            params["price1"] = price_min
        if price_max:
            params["price2"] = price_max

        # Не используем фильтр по марке в API — фильтруем сами после получения
        pass

        url = f"{RESTAPP_BASE}/ads"
        logger.info(f"Запрос API: {date1.strftime('%H:%M')} — {date2.strftime('%H:%M')}, марка={brand or 'все'}")

        try:
            resp = await self.client.get(url, params=params)

            if resp.status_code != 200:
                logger.warning(f"Статус {resp.status_code}: {resp.text[:300]}")
                return []

            data = resp.json()

            if isinstance(data, dict) and data.get("status") == "error":
                logger.error(f"Ошибка API: {data.get('message')}")
                return []

            ads = data.get("data", []) if isinstance(data, dict) else data
            logger.info(f"Получено: {len(ads)} объявлений")

            # Обновляем время последней проверки
            self.last_check_time = date2

            listings = []
            for ad in ads:
                listing = self._extract_listing(ad)
                if listing:
                    listings.append(listing)

            logger.info(f"Обработано: {len(listings)}")
            return listings

        except Exception as e:
            logger.error(f"Ошибка API: {type(e).__name__}: {e}")
            return []

    def _extract_listing(self, ad: dict) -> Optional[dict]:
        try:
            avito_id = str(ad.get("Id") or ad.get("id") or "")
            if not avito_id:
                return None

            title = ad.get("title", "") or ""
            
            price = 0
            try:
                price = int(str(ad.get("price", 0)).replace(" ", "").replace("₽", ""))
            except:
                pass

            url = ad.get("url") or ""
            if not url or url == "hidden_in_demo":
                avito_url_id = ad.get("avito_id", "")
                url = f"https://www.avito.ru/items/{avito_url_id}" if avito_url_id else ""

            description = ad.get("description") or ""
            city = ad.get("city") or ""
            region_name = ad.get("region") or ""
            location = city or region_name

            # Фото через запятую
            photos = []
            images_raw = ad.get("images") or ad.get("images_big") or ""
            if isinstance(images_raw, str) and images_raw:
                photos = [p.strip() for p in images_raw.split(",") 
                         if p.strip().startswith("http")][:10]
            elif isinstance(images_raw, list):
                photos = [p for p in images_raw 
                         if isinstance(p, str) and p.startswith("http")][:10]

            # Год — прямое поле или из params
            year = None
            year_raw = ad.get("year")
            if year_raw:
                try:
                    y = int(str(year_raw))
                    if 1900 <= y <= 2030:
                        year = y
                except:
                    pass
            if not year:
                for param in (ad.get("params") or []):
                    if isinstance(param, dict):
                        name = param.get("name", "").lower()
                        if "год выпуска" in name or name == "год":
                            try:
                                year = int(param.get("value", ""))
                                break
                            except:
                                pass
            if not year:
                m = re.search(r"\b(19|20)\d{2}\b", title)
                if m:
                    year = int(m.group())

            # Пробег — из body или params
            mileage = None
            body_raw = ad.get("body") or ""
            if body_raw and "км" in str(body_raw).lower():
                mileage_clean = re.sub(r"[^\d]", "", str(body_raw))
                if mileage_clean:
                    mileage = int(mileage_clean)
            if not mileage:
                for param in (ad.get("params") or []):
                    if isinstance(param, dict):
                        name = param.get("name", "").lower()
                        if "пробег" in name:
                            val = str(param.get("value", "")).split("-")[0]
                            mileage_clean = re.sub(r"[^\d]", "", val)
                            if mileage_clean:
                                mileage = int(mileage_clean)
                                break
            if not mileage:
                m = re.search(r"([\d\s]+)\s*км", title)
                if m:
                    mileage_clean = re.sub(r"\s", "", m.group(1))
                    if mileage_clean:
                        mileage = int(mileage_clean)

            # Марка и модель
            brand = ad.get("marka") or ad.get("brand") or ad.get("mark")
            model = ad.get("model")
            if not brand:
                brand, model = self.parse_brand_model_from_title(title)

            # Продавец
            seller_name = ad.get("name") or ""
            postfix = ad.get("postfix") or ""
            seller_type = "dealer" if "компани" in postfix.lower() else "private"

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
                "location": location,
                "photos": photos,
                "seller_name": seller_name,
                "seller_type": seller_type,
                "seller_listings_count": 1,
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
        if not url or "hidden_in_demo" in url:
            return None
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
                    if isinstance(ad, list) and ad:
                        ad = ad[0]
                    return {
                        "description": ad.get("description", ""),
                        "photos": ad.get("photos", []),
                    }
        except Exception as e:
            logger.error(f"Ошибка деталей {url}: {e}")
        return None

    def parse_brand_model_from_title(self, title: str) -> tuple:
        brands = [
            "Toyota", "Honda", "Kia", "Hyundai", "Nissan", "Mazda",
            "BMW", "Mercedes", "Audi", "Volkswagen", "Skoda", "Lada",
            "Renault", "Ford", "Chevrolet", "Lexus", "Infiniti",
            "Subaru", "Mitsubishi", "Suzuki", "Volvo", "Jeep",
            "Porsche", "Land Rover", "Jaguar", "Chery", "Geely",
            "Haval", "Exeed", "Omoda", "Kaiyi", "Tank", "Belgee",
            "Jaecoo", "Voyah", "Zeekr", "Nordcross", "TENET",
            "GAC", "Wey", "Lynk", "Jetour", "Qiyuan", "HAVAL",
            "Bentley", "Porsche", "Lamborghini", "Ferrari"
        ]
        title_lower = title.lower()
        for brand in brands:
            if brand.lower() in title_lower:
                idx = title_lower.index(brand.lower()) + len(brand)
                rest = title[idx:].strip().split()
                model = rest[0] if rest else None
                return brand, model
        return None, None

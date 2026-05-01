import asyncio
import random
import re
from typing import Optional
import httpx
from fake_useragent import UserAgent
from loguru import logger
import os

PROXY_URL = os.getenv("PROXY_URL")

ua = UserAgent()

URGENT_KEYWORDS = [
    "срочно", "уезжаю", "переезд", "нужны деньги",
    "вынужден продать", "торг уместен", "торг при осмотре",
    "рассмотрю предложения", "обмен", "рассмотрю обмен"
]

DEALER_KEYWORDS = [
    "автосалон", "официальный дилер", "трейд-ин", "лизинг"
]

AVITO_API = "https://www.avito.ru/web/1/main/items"

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.avito.ru/",
}


class AvitoParser:
    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None

    async def start(self):
        proxies = {"all://": PROXY_URL} if PROXY_URL else None
        self.client = httpx.AsyncClient(
            headers=HEADERS,
            proxies=proxies,
            timeout=30,
            follow_redirects=True
        )
        logger.info("Парсер запущен")

    async def stop(self):
        if self.client:
            await self.client.aclose()
        logger.info("Парсер остановлен")

    async def random_delay(self):
        await asyncio.sleep(random.uniform(2.0, 5.0))

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
        url = self._build_search_url(
            brand, model, price_min, price_max,
            year_min, year_max, region, radius, page
        )
        logger.info(f"Парсим: {url}")

        try:
            self.client.headers["User-Agent"] = ua.random
            resp = await self.client.get(url)
            await self.random_delay()

            if resp.status_code != 200:
                logger.warning(f"Статус {resp.status_code}")
                return []

            data = resp.json()
            items = data.get("items", [])
            listings = []

            for item in items:
                listing = self._extract_item(item)
                if listing:
                    listings.append(listing)

            logger.info(f"Найдено: {len(listings)}")
            return listings

        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}")
            return []

    def _extract_item(self, item: dict) -> Optional[dict]:
        try:
            avito_id = str(item.get("id", ""))
            if not avito_id:
                return None

            title = item.get("title", "")
            price_raw = item.get("price", {})
            price = price_raw.get("value", {}).get("raw", 0)
            url = "https://www.avito.ru" + item.get("urlPath", "")
            location = item.get("location", {}).get("name", "")
            images = [
                img.get("864x648", img.get("432x324", ""))
                for img in item.get("images", [])[:10]
            ]

            params = item.get("iva", {})
            year = None
            mileage = None

            for param in params.get("AutoParamsStep", []):
                val = param.get("payload", {}).get("value", "")
                name = param.get("payload", {}).get("name", "")
                if "год" in name.lower():
                    try:
                        year = int(val)
                    except:
                        pass
                if "пробег" in name.lower():
                    mileage_clean = re.sub(r"[^\d]", "", val)
                    if mileage_clean:
                        mileage = int(mileage_clean)

            seller = item.get("seller", {})
            seller_name = seller.get("name", "")
            seller_type = "dealer" if seller.get("type") == "company" else "private"
            seller_count = seller.get("itemsCount", 1)

            full_text = title.lower()
            urgent_keywords = [kw for kw in URGENT_KEYWORDS if kw in full_text]
            is_urgent = len(urgent_keywords) > 0
            has_dealer = any(kw in full_text for kw in DEALER_KEYWORDS)

            brand, model_name = self.parse_brand_model_from_title(title)

            return {
                "avito_id": avito_id,
                "url": url,
                "title": title,
                "price": price,
                "year": year,
                "mileage": mileage,
                "location": location,
                "photos": images,
                "seller_name": seller_name,
                "seller_type": seller_type,
                "seller_listings_count": seller_count,
                "brand": brand,
                "model": model_name,
                "urgent_keywords": urgent_keywords,
                "is_urgent": is_urgent,
                "has_dealer_keywords": has_dealer,
            }
        except Exception as e:
            logger.error(f"Ошибка извлечения данных: {e}")
            return None

    def _build_search_url(
        self, brand, model, price_min, price_max,
        year_min, year_max, region, radius, page
    ) -> str:
        base = f"https://www.avito.ru/{region or 'rossiya'}/avtomobili"
        if brand:
            base += f"/{brand.lower()}"
        if model:
            base += f"/{model.lower()}"

        params = ["sort=date", "s=104"]
        if price_min:
            params.append(f"pmin={price_min}")
        if price_max:
            params.append(f"pmax={price_max}")
        if year_min:
            params.append(f"ym_from={year_min}")
        if year_max:
            params.append(f"ym_to={year_max}")
        if radius:
            params.append(f"radius={radius}")
        if page > 1:
            params.append(f"p={page}")

        return f"{base}?{'&'.join(params)}"

    async def parse_listing_detail(self, url: str) -> Optional[dict]:
        try:
            self.client.headers["User-Agent"] = ua.random
            resp = await self.client.get(url)
            await self.random_delay()

            if resp.status_code != 200:
                return None

            html = resp.text
            description = ""
            desc_match = re.search(
                r'"description":"(.*?)"(?:,|\})', html
            )
            if desc_match:
                description = desc_match.group(1).encode().decode("unicode_escape")

            return {"description": description}

        except Exception as e:
            logger.error(f"Ошибка деталей {url}: {e}")
            return None

    def parse_brand_model_from_title(self, title: str) -> tuple:
        brands = [
            "Toyota", "Honda", "Kia", "Hyundai", "Nissan", "Mazda",
            "BMW", "Mercedes", "Audi", "Volkswagen", "Skoda", "Lada",
            "Renault", "Ford", "Chevrolet", "Lexus", "Infiniti",
            "Subaru", "Mitsubishi", "Suzuki", "Volvo", "Jeep"
        ]
        title_lower = title.lower()
        for brand in brands:
            if brand.lower() in title_lower:
                idx = title_lower.index(brand.lower()) + len(brand)
                rest = title[idx:].strip().split()
                model = rest[0] if rest else None
                return brand, model
        return None, None

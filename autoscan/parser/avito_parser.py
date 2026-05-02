import asyncio
import random
import re
import json
from typing import Optional
import httpx
from bs4 import BeautifulSoup
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

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
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

            # Парсим HTML страницу
            listings = await self._parse_html_page(resp.text)
            logger.info(f"Найдено: {len(listings)}")
            return listings

        except Exception as e:
            logger.error(f"Ошибка парсинга: {type(e).__name__}: {e}")
            return []

    async def _parse_html_page(self, html: str) -> list[dict]:
        """Парсинг HTML страницы Авито"""
        listings = []

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Способ 1 — ищем данные в script тегах
            scripts = soup.find_all("script")
            for script in scripts:
                if not script.string:
                    continue
                text = script.string.strip()

                # Авито хранит данные объявлений в window.__initialData__ или похожих переменных
                if '"items"' in text and '"title"' in text:
                    try:
                        # Пробуем извлечь JSON из скрипта
                        matches = re.findall(r'\{[^{}]*"items"[^{}]*\}', text)
                        for match in matches:
                            try:
                                data = json.loads(match)
                                if "items" in data:
                                    for item in data["items"]:
                                        listing = self._extract_from_json(item)
                                        if listing:
                                            listings.append(listing)
                            except:
                                continue
                    except:
                        continue

            # Способ 2 — прямой парсинг карточек из HTML
            if not listings:
                cards = soup.find_all("div", attrs={"data-marker": "item"})
                logger.info(f"Найдено карточек в HTML: {len(cards)}")

                for card in cards:
                    try:
                        listing = self._extract_from_html_card(card)
                        if listing:
                            listings.append(listing)
                    except Exception as e:
                        logger.error(f"Ошибка карточки: {e}")
                        continue

            # Способ 3 — ищем через article теги
            if not listings:
                cards = soup.find_all("article")
                logger.info(f"Найдено article: {len(cards)}")
                for card in cards:
                    try:
                        listing = self._extract_from_html_card(card)
                        if listing:
                            listings.append(listing)
                    except:
                        continue

        except Exception as e:
            logger.error(f"Ошибка HTML парсинга: {e}")

        return listings

    def _extract_from_html_card(self, card) -> Optional[dict]:
        """Извлечение данных из HTML карточки"""
        # ID объявления
        avito_id = card.get("data-item-id") or card.get("id", "")
        if not avito_id:
            # Пробуем найти в дочерних элементах
            link = card.find("a", href=re.compile(r"/\w+_\d+"))
            if link:
                href = link.get("href", "")
                id_match = re.search(r"_(\d+)$", href)
                if id_match:
                    avito_id = id_match.group(1)

        if not avito_id:
            return None

        # Заголовок и ссылка
        title = ""
        url = ""
        title_el = (
            card.find(attrs={"data-marker": "item-title"}) or
            card.find("h3") or
            card.find("h2") or
            card.find("a", href=re.compile(r"/avtomobili/"))
        )
        if title_el:
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href:
                url = f"https://www.avito.ru{href}" if href.startswith("/") else href

        if not title:
            return None

        # Цена
        price = 0
        price_el = card.find(attrs={"data-marker": "item-price"})
        if not price_el:
            price_el = card.find(class_=re.compile(r"price", re.I))
        if price_el:
            price_text = price_el.get_text(strip=True)
            price_clean = re.sub(r"[^\d]", "", price_text)
            price = int(price_clean) if price_clean else 0

        # Локация
        location = ""
        geo_el = (
            card.find(attrs={"data-marker": "item-address"}) or
            card.find(class_=re.compile(r"geo|location|address", re.I))
        )
        if geo_el:
            location = geo_el.get_text(strip=True)

        # Параметры авто (год, пробег)
        year = None
        mileage = None
        params_el = card.find(attrs={"data-marker": "item-specific-params"})
        if params_el:
            params_text = params_el.get_text(strip=True)
            year_match = re.search(r"\b(19|20)\d{2}\b", params_text)
            if year_match:
                year = int(year_match.group())
            mileage_match = re.search(r"([\d\s]+)\s*км", params_text)
            if mileage_match:
                mileage_str = re.sub(r"\s", "", mileage_match.group(1))
                mileage = int(mileage_str) if mileage_str else None

        # Фото
        photos = []
        img_els = card.find_all("img")
        for img in img_els:
            src = img.get("src") or img.get("data-src", "")
            if src and "avito" in src and not src.endswith(".svg"):
                photos.append(src)

        # Продавец
        seller_type = "private"
        seller_name = ""
        seller_el = card.find(attrs={"data-marker": "item-seller"})
        if seller_el:
            seller_text = seller_el.get_text(strip=True).lower()
            if any(kw in seller_text for kw in ["компания", "салон", "дилер"]):
                seller_type = "dealer"
            seller_name = seller_el.get_text(strip=True)

        # Ключевые слова
        full_text = f"{title}".lower()
        urgent_keywords = [kw for kw in URGENT_KEYWORDS if kw in full_text]
        is_urgent = len(urgent_keywords) > 0
        has_dealer = any(kw in full_text for kw in DEALER_KEYWORDS)

        brand, model = self.parse_brand_model_from_title(title)

        return {
            "avito_id": str(avito_id),
            "url": url,
            "title": title,
            "price": price,
            "year": year,
            "mileage": mileage,
            "location": location,
            "photos": photos[:10],
            "seller_name": seller_name,
            "seller_type": seller_type,
            "seller_listings_count": 1,
            "brand": brand,
            "model": model,
            "urgent_keywords": urgent_keywords,
            "is_urgent": is_urgent,
            "has_dealer_keywords": has_dealer,
        }

    def _extract_from_json(self, item: dict) -> Optional[dict]:
        """Извлечение данных из JSON объекта"""
        try:
            avito_id = str(item.get("id", ""))
            if not avito_id:
                return None

            title = item.get("title", "")
            price_raw = item.get("price", {})
            if isinstance(price_raw, dict):
                price = price_raw.get("value", {}).get("raw", 0)
            else:
                price = int(price_raw) if price_raw else 0

            url = "https://www.avito.ru" + item.get("urlPath", "")
            location = item.get("location", {}).get("name", "") if isinstance(item.get("location"), dict) else ""
            images = []
            for img in item.get("images", [])[:10]:
                if isinstance(img, dict):
                    src = img.get("864x648") or img.get("432x324") or ""
                    if src:
                        images.append(src)

            year = None
            mileage = None
            for param in item.get("iva", {}).get("AutoParamsStep", []):
                val = param.get("payload", {}).get("value", "")
                name = param.get("payload", {}).get("name", "").lower()
                if "год" in name:
                    try:
                        year = int(val)
                    except:
                        pass
                if "пробег" in name:
                    mileage_clean = re.sub(r"[^\d]", "", str(val))
                    if mileage_clean:
                        mileage = int(mileage_clean)

            seller = item.get("seller", {})
            seller_name = seller.get("name", "") if isinstance(seller, dict) else ""
            seller_type = "dealer" if (isinstance(seller, dict) and seller.get("type") == "company") else "private"
            seller_count = seller.get("itemsCount", 1) if isinstance(seller, dict) else 1

            full_text = title.lower()
            urgent_keywords = [kw for kw in URGENT_KEYWORDS if kw in full_text]
            is_urgent = len(urgent_keywords) > 0
            has_dealer = any(kw in full_text for kw in DEALER_KEYWORDS)

            brand, model = self.parse_brand_model_from_title(title)

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
                "model": model,
                "urgent_keywords": urgent_keywords,
                "is_urgent": is_urgent,
                "has_dealer_keywords": has_dealer,
            }
        except Exception as e:
            logger.error(f"Ошибка JSON извлечения: {e}")
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

        params = ["sort=date"]
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

            soup = BeautifulSoup(resp.text, "html.parser")
            description = ""

            desc_el = soup.find(attrs={"data-marker": "item-view/item-description"})
            if desc_el:
                description = desc_el.get_text(strip=True)

            return {"description": description}

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
            "Haval", "Exeed", "Omoda"
        ]
        title_lower = title.lower()
        for brand in brands:
            if brand.lower() in title_lower:
                idx = title_lower.index(brand.lower()) + len(brand)
                rest = title[idx:].strip().split()
                model = rest[0] if rest else None
                return brand, model
        return None, None

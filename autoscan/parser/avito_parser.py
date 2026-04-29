import asyncio
import random
import re
from datetime import datetime
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext
from fake_useragent import UserAgent
from loguru import logger
import os
from dotenv import load_dotenv

load_dotenv()

PROXY_URL = os.getenv("PROXY_URL")

# Случайные задержки чтобы не выглядеть как бот
DELAY_MIN = 2.0
DELAY_MAX = 6.0

# Ключевые слова срочности
URGENT_KEYWORDS = [
    "срочно", "срочная продажа", "уезжаю", "переезд",
    "нужны деньги", "вынужден продать", "быстро продам",
    "торг уместен", "торг при осмотре", "рассмотрю предложения",
    "обмен", "рассмотрю обмен", "обмен с доплатой"
]

# Стоп-слова (дилеры)
DEALER_KEYWORDS = [
    "автосалон", "официальный дилер", "трейд-ин",
    "кредит от", "лизинг", "гарантия дилера"
]

ua = UserAgent()


class AvitoParser:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def start(self):
        """Запуск браузера"""
        playwright = await async_playwright().start()
        
        launch_options = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        }
        
        if PROXY_URL:
            launch_options["proxy"] = {"server": PROXY_URL}
        
        self.browser = await playwright.chromium.launch(**launch_options)
        self.context = await self.browser.new_context(
            user_agent=ua.random,
            viewport={"width": 1366, "height": 768},
            locale="ru-RU"
        )
        # Скрываем признаки автоматизации
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
        """)
        logger.info("Браузер запущен")

    async def stop(self):
        """Остановка браузера"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        logger.info("Браузер остановлен")

    async def random_delay(self):
        """Случайная задержка между запросами"""
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        await asyncio.sleep(delay)

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
        Парсинг страницы поиска авто на Авито
        Возвращает список базовых данных объявлений
        """
        url = self._build_search_url(
            brand, model, price_min, price_max,
            year_min, year_max, region, radius, page
        )
        
        logger.info(f"Парсим страницу: {url}")
        
        page_obj = await self.context.new_page()
        listings = []
        
        try:
            await page_obj.goto(url, wait_until="networkidle", timeout=30000)
            await self.random_delay()
            
            # Проверяем на капчу
            if await self._check_captcha(page_obj):
                logger.warning("Обнаружена капча — пропускаем страницу")
                return []
            
            # Ищем карточки объявлений
            cards = await page_obj.query_selector_all('[data-marker="item"]')
            logger.info(f"Найдено карточек: {len(cards)}")
            
            for card in cards:
                try:
                    listing = await self._extract_card_data(card)
                    if listing:
                        listings.append(listing)
                except Exception as e:
                    logger.error(f"Ошибка парсинга карточки: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Ошибка парсинга страницы {url}: {e}")
        finally:
            await page_obj.close()
        
        return listings

    async def parse_listing_detail(self, url: str) -> Optional[dict]:
        """
        Парсинг детальной страницы объявления
        Получаем полное описание, все фото, данные продавца
        """
        page_obj = await self.context.new_page()
        
        try:
            await page_obj.goto(url, wait_until="networkidle", timeout=30000)
            await self.random_delay()
            
            if await self._check_captcha(page_obj):
                logger.warning(f"Капча на {url}")
                return None
            
            # Извлекаем данные
            data = {}
            
            # Описание
            desc_el = await page_obj.query_selector('[data-marker="item-view/item-description"]')
            if desc_el:
                data["description"] = await desc_el.inner_text()
            
            # Все фотографии
            photos = []
            photo_els = await page_obj.query_selector_all('img[data-marker="image"]')
            for ph in photo_els:
                src = await ph.get_attribute("src")
                if src and "avito" in src:
                    # Получаем URL оригинального размера
                    src = re.sub(r'\d+x\d+', '1280x960', src)
                    photos.append(src)
            data["photos"] = photos[:10]  # максимум 10 фото
            
            # Характеристики авто из таблицы
            params = {}
            param_els = await page_obj.query_selector_all('[data-marker="item-view/item-params"] li')
            for el in param_els:
                text = await el.inner_text()
                if ":" in text:
                    key, val = text.split(":", 1)
                    params[key.strip()] = val.strip()
            data["params"] = params
            
            # Данные продавца
            seller = {}
            seller_el = await page_obj.query_selector('[data-marker="seller-info/name"]')
            if seller_el:
                seller["name"] = await seller_el.inner_text()
            
            # Количество объявлений продавца
            seller_count_el = await page_obj.query_selector('[data-marker="seller-info/seller-summary"]')
            if seller_count_el:
                count_text = await seller_count_el.inner_text()
                numbers = re.findall(r'\d+', count_text)
                if numbers:
                    seller["listings_count"] = int(numbers[0])
            
            data["seller"] = seller
            
            # Дата публикации
            date_el = await page_obj.query_selector('[data-marker="item-view/item-date"]')
            if date_el:
                data["published_date"] = await date_el.inner_text()
            
            return data
            
        except Exception as e:
            logger.error(f"Ошибка парсинга детали {url}: {e}")
            return None
        finally:
            await page_obj.close()

    async def _extract_card_data(self, card) -> Optional[dict]:
        """Извлечение данных из карточки в поиске"""
        data = {}
        
        # ID объявления
        item_id = await card.get_attribute("data-item-id")
        if not item_id:
            return None
        data["avito_id"] = item_id
        
        # Ссылка
        link_el = await card.query_selector('[data-marker="item-title"]')
        if link_el:
            href = await link_el.get_attribute("href")
            data["url"] = f"https://www.avito.ru{href}" if href else None
            data["title"] = await link_el.inner_text()
        
        # Цена
        price_el = await card.query_selector('[data-marker="item-price"]')
        if price_el:
            price_text = await price_el.inner_text()
            price = re.sub(r'[^\d]', '', price_text)
            data["price"] = int(price) if price else None
        
        # Локация и дата
        geo_el = await card.query_selector('[data-marker="item-address"]')
        if geo_el:
            data["location"] = await geo_el.inner_text()
        
        date_el = await card.query_selector('[data-marker="item-date"]')
        if date_el:
            data["date_text"] = await date_el.inner_text()
        
        # Основные характеристики из карточки
        params_el = await card.query_selector('[data-marker="item-specific-params"]')
        if params_el:
            data["params_text"] = await params_el.inner_text()
            # Парсим год и пробег из текста типа "2019, 87 000 км"
            params_text = data["params_text"]
            year_match = re.search(r'\b(19|20)\d{2}\b', params_text)
            if year_match:
                data["year"] = int(year_match.group())
            mileage_match = re.search(r'([\d\s]+)\s*км', params_text)
            if mileage_match:
                mileage = re.sub(r'\s', '', mileage_match.group(1))
                data["mileage"] = int(mileage) if mileage else None
        
        # Фото превью
        img_el = await card.query_selector('img[data-marker="item-photo"]')
        if img_el:
            src = await img_el.get_attribute("src")
            data["preview_photo"] = src
        
        # Детектируем ключевые слова уже на этапе карточки
        full_text = f"{data.get('title', '')} {data.get('params_text', '')}".lower()
        data["urgent_keywords"] = [kw for kw in URGENT_KEYWORDS if kw in full_text]
        data["is_urgent"] = len(data["urgent_keywords"]) > 0
        data["has_dealer_keywords"] = any(kw in full_text for kw in DEALER_KEYWORDS)
        
        return data

    def _build_search_url(
        self, brand, model, price_min, price_max,
        year_min, year_max, region, radius, page
    ) -> str:
        """Строим URL поиска Авито"""
        base = f"https://www.avito.ru/{region}/avtomobili"
        
        params = []
        
        if brand:
            # Авито использует slug марки в URL
            brand_slug = brand.lower().replace(" ", "_")
            base = f"{base}/{brand_slug}"
        
        if model:
            model_slug = model.lower().replace(" ", "_")
            base = f"{base}/{model_slug}"
        
        query_params = []
        
        if price_min:
            query_params.append(f"pmin={price_min}")
        if price_max:
            query_params.append(f"pmax={price_max}")
        if year_min:
            query_params.append(f"ym_from={year_min}")
        if year_max:
            query_params.append(f"ym_to={year_max}")
        if radius:
            query_params.append(f"radius={radius}")
        if page > 1:
            query_params.append(f"p={page}")
        
        # Сортировка по дате — сначала новые
        query_params.append("sort=date")
        
        if query_params:
            return f"{base}?{'&'.join(query_params)}"
        return base

    async def _check_captcha(self, page) -> bool:
        """Проверка наличия капчи"""
        captcha_el = await page.query_selector('[data-marker="captcha"]')
        if captcha_el:
            return True
        title = await page.title()
        if "captcha" in title.lower() or "robota" in title.lower():
            return True
        return False

    def parse_brand_model_from_title(self, title: str) -> tuple[str, str]:
        """Извлечение марки и модели из заголовка"""
        # Список популярных марок
        brands = [
            "Toyota", "Honda", "Kia", "Hyundai", "Nissan", "Mazda",
            "BMW", "Mercedes", "Audi", "Volkswagen", "Skoda", "Lada",
            "Renault", "Ford", "Chevrolet", "Lexus", "Infiniti", "Subaru",
            "Mitsubishi", "Suzuki", "Volvo", "Land Rover", "Jeep"
        ]
        
        title_lower = title.lower()
        found_brand = None
        found_model = None
        
        for brand in brands:
            if brand.lower() in title_lower:
                found_brand = brand
                # Пытаемся найти модель после марки
                idx = title_lower.index(brand.lower()) + len(brand)
                rest = title[idx:].strip()
                # Берём первые 1-2 слова как модель
                parts = rest.split()
                if parts:
                    found_model = parts[0]
                break
        
        return found_brand, found_model

import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from parser.avito_parser import AvitoParser
from analyzer.ai_analyzer import analyze_listing, get_market_price
from database.connection import AsyncSessionLocal
from database.models import Listing, PriceHistory, User, UserFilter
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import insert

# Минимальный скоринг для отправки алерта
MIN_SCORE_FOR_ALERT = 6.0

parser = AvitoParser()
scheduler = AsyncIOScheduler()


async def run_parse_cycle():
    """
    Основной цикл парсинга:
    1. Получаем все активные фильтры пользователей
    2. Объединяем похожие запросы
    3. Парсим Авито
    4. Анализируем новые объявления
    5. Отправляем алерты
    """
    logger.info("=== Старт цикла парсинга ===")
    
    async with AsyncSessionLocal() as session:
        # Получаем все активные фильтры
        result = await session.execute(
            select(UserFilter, User).join(User).where(
                and_(UserFilter.is_active == True, User.is_active == True)
            )
        )
        filter_rows = result.all()
    
    if not filter_rows:
        logger.info("Нет активных фильтров")
        return
    
    logger.info(f"Обрабатываем {len(filter_rows)} фильтров")
    
    # Группируем фильтры по параметрам поиска чтобы не дублировать запросы
    search_groups = _group_filters(filter_rows)
    
    for search_params, filters_and_users in search_groups.items():
        try:
            await _process_search_group(search_params, filters_and_users)
            # Задержка между группами
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Ошибка обработки группы {search_params}: {e}")
    
    logger.info("=== Цикл парсинга завершён ===")


async def _process_search_group(search_params: tuple, filters_and_users: list):
    """Обработка одной группы поиска"""
    brand, region = search_params
    
    logger.info(f"Парсим: марка={brand}, регион={region}")
    
    # Парсим первые 3 страницы (новые объявления)
    all_listings = []
    for page in range(1, 4):
        listings = await parser.parse_search_page(
            brand=brand,
            region=_city_to_slug(region),
            page=page
        )
        all_listings.extend(listings)
        if len(listings) < 10:  # нет больше страниц
            break
        await asyncio.sleep(3)
    
    logger.info(f"Спарсили {len(all_listings)} объявлений")
    
    # Обрабатываем каждое объявление
    new_count = 0
    for listing_data in all_listings:
        try:
            is_new = await _process_listing(listing_data)
            if is_new:
                new_count += 1
        except Exception as e:
            logger.error(f"Ошибка обработки объявления: {e}")
    
    logger.info(f"Новых объявлений: {new_count}")
    
    # Проверяем подходят ли новые объявления под фильтры пользователей
    if new_count > 0:
        await _check_and_send_alerts(filters_and_users)


async def _process_listing(listing_data: dict) -> bool:
    """
    Обработка одного объявления.
    Возвращает True если объявление новое.
    """
    avito_id = listing_data.get("avito_id")
    if not avito_id:
        return False
    
    async with AsyncSessionLocal() as session:
        # Проверяем есть ли уже в базе
        result = await session.execute(
            select(Listing).where(Listing.avito_id == avito_id)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # Обновляем цену если изменилась
            new_price = listing_data.get("price")
            if new_price and existing.price != new_price:
                # Сохраняем в историю
                history = PriceHistory(
                    listing_id=existing.id,
                    price=new_price
                )
                session.add(history)
                existing.price = new_price
                await session.commit()
                logger.info(f"Цена изменилась: {avito_id} → {new_price}")
            return False
        
        # Парсим детали объявления
        url = listing_data.get("url")
        if url:
            details = await parser.parse_listing_detail(url)
            if details:
                listing_data.update(details)
        
        # Парсим марку и модель
        brand, model = parser.parse_brand_model_from_title(
            listing_data.get("title", "")
        )
        
        # Получаем рыночную цену
        market_price = 0
        if brand and listing_data.get("year") and listing_data.get("mileage"):
            market_price = await get_market_price(
                brand=brand,
                model=model or "",
                year=listing_data["year"],
                mileage=listing_data["mileage"],
                region=listing_data.get("location", "Россия")
            )
        
        # Вычисляем отклонение от рынка
        price = listing_data.get("price", 0)
        price_diff_pct = None
        if market_price and price:
            price_diff_pct = ((price - market_price) / market_price) * 100
        
        # Определяем тип продавца
        seller_info = listing_data.get("seller", {})
        seller_listings_count = seller_info.get("listings_count", 1)
        is_reseller = (
            listing_data.get("has_dealer_keywords", False) or
            seller_listings_count > 5
        )
        
        # Проверяем дубли (по похожим параметрам)
        if brand and listing_data.get("year") and listing_data.get("mileage"):
            dup_result = await session.execute(
                select(Listing).where(
                    and_(
                        Listing.brand == brand,
                        Listing.year == listing_data.get("year"),
                        Listing.mileage == listing_data.get("mileage"),
                        Listing.price == price,
                        Listing.avito_id != avito_id
                    )
                )
            )
            is_duplicate = dup_result.scalar_one_or_none() is not None
        else:
            is_duplicate = False
        
        # Создаём объявление в базе
        new_listing = Listing(
            avito_id=avito_id,
            url=url or "",
            title=listing_data.get("title", ""),
            price=price,
            description=listing_data.get("description", ""),
            brand=brand,
            model=model,
            year=listing_data.get("year"),
            mileage=listing_data.get("mileage"),
            region=listing_data.get("location", ""),
            photos=listing_data.get("photos", []),
            seller_name=seller_info.get("name", ""),
            seller_type="dealer" if is_reseller else "private",
            seller_listings_count=seller_listings_count,
            market_price=market_price if market_price else None,
            price_diff_pct=price_diff_pct,
            is_urgent=listing_data.get("is_urgent", False),
            urgent_keywords=listing_data.get("urgent_keywords", []),
            is_reseller=is_reseller,
            is_duplicate=is_duplicate,
        )
        session.add(new_listing)
        await session.flush()  # получаем id
        
        # Сохраняем начальную цену в историю
        history = PriceHistory(listing_id=new_listing.id, price=price)
        session.add(history)
        
        await session.commit()
        listing_id = new_listing.id
    
    # AI-анализ (делаем асинхронно после сохранения)
    if not is_duplicate:
        await _analyze_and_update(listing_id, listing_data, market_price)
    
    return True


async def _analyze_and_update(listing_id: int, listing_data: dict, market_price: int):
    """AI-анализ объявления и обновление в базе"""
    try:
        analysis = await analyze_listing(listing_data, market_price)
        score = analysis.get("score", 0)
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Listing).where(Listing.id == listing_id)
            )
            listing = result.scalar_one_or_none()
            if listing:
                listing.score = score
                listing.ai_analysis = analysis
                listing.analyzed_at = datetime.now()
                
                # Обновляем детекторы из AI анализа
                if analysis.get("seller_analysis", {}).get("is_reseller"):
                    listing.is_reseller = True
                if analysis.get("photo_analysis", {}).get("is_stock_photo"):
                    listing.is_stock_photo = True
                
                await session.commit()
                
    except Exception as e:
        logger.error(f"Ошибка AI-анализа листинга {listing_id}: {e}")


async def _check_and_send_alerts(filters_and_users: list):
    """Проверяем новые объявления под фильтры и отправляем алерты"""
    from bot.main import send_alert
    
    async with AsyncSessionLocal() as session:
        for user_filter, user in filters_and_users:
            try:
                # Строим запрос с учётом фильтра
                query = select(Listing).where(
                    and_(
                        Listing.score >= max(user_filter.min_score, MIN_SCORE_FOR_ALERT),
                        Listing.is_duplicate == False,
                        Listing.analyzed_at.isnot(None)
                    )
                )
                
                # Фильтр по маркам
                if user_filter.brands:
                    query = query.where(Listing.brand.in_(user_filter.brands))
                
                # Фильтр по цене
                if user_filter.price_min:
                    query = query.where(Listing.price >= user_filter.price_min)
                if user_filter.price_max:
                    query = query.where(Listing.price <= user_filter.price_max)
                
                # Фильтр по году
                if user_filter.year_min:
                    query = query.where(Listing.year >= user_filter.year_min)
                if user_filter.year_max:
                    query = query.where(Listing.year <= user_filter.year_max)
                
                # Только свежие (последние 30 минут)
                from datetime import timedelta
                cutoff = datetime.now() - timedelta(minutes=35)
                query = query.where(Listing.parsed_at >= cutoff)
                
                result = await session.execute(query)
                matching_listings = result.scalars().all()
                
                for listing in matching_listings:
                    # Проверяем ключевые слова если заданы
                    if user_filter.keywords_include:
                        text = f"{listing.title} {listing.description or ''}".lower()
                        if not any(kw in text for kw in user_filter.keywords_include):
                            continue
                    
                    # Отправляем алерт
                    await send_alert(
                        user.telegram_id,
                        listing,
                        listing.ai_analysis or {}
                    )
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Ошибка отправки алертов пользователю {user.telegram_id}: {e}")


def _group_filters(filter_rows: list) -> dict:
    """Группировка фильтров по параметрам поиска"""
    groups = {}
    
    for user_filter, user in filter_rows:
        brands = user_filter.brands or [None]
        regions = user_filter.regions or ["moskva"]
        
        for brand in brands:
            for region in regions:
                key = (brand, region)
                if key not in groups:
                    groups[key] = []
                groups[key].append((user_filter, user))
    
    return groups


def _city_to_slug(city: str) -> str:
    """Конвертация названия города в slug для Авито URL"""
    mapping = {
        "Москва": "moskva",
        "Санкт-Петербург": "sankt-peterburg",
        "Казань": "kazan",
        "Новосибирск": "novosibirsk",
        "Екатеринбург": "ekaterinburg",
        "Краснодар": "krasnodar",
        "Самара": "samara",
        "Ростов-на-Дону": "rostov-na-donu",
    }
    return mapping.get(city, city.lower().replace(" ", "-") if city else "rossiya")


async def start_scheduler():
    """Запуск планировщика"""
    await parser.start()
    
    # Основной цикл каждые 30 минут
    scheduler.add_job(
        run_parse_cycle,
        "interval",
        minutes=30,
        id="main_parse",
        next_run_time=datetime.now()  # запускаем сразу при старте
    )
    
    scheduler.start()
    logger.info("Планировщик запущен")


async def stop_scheduler():
    """Остановка планировщика"""
    scheduler.shutdown()
    await parser.stop()
    logger.info("Планировщик остановлен")

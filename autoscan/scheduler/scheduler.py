import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from parser.avito_parser import AvitoParser
from analyzer.ai_analyzer import analyze_listing, get_market_price
from database.connection import AsyncSessionLocal
from database.models import Listing, PriceHistory, User, UserFilter
from sqlalchemy import select, and_

MIN_SCORE_FOR_ALERT = 1.0

parser = AvitoParser()
scheduler = AsyncIOScheduler()

CITY_SLUGS = {
    "москва": "moskva",
    "moscow": "moskva",
    "moskva": "moskva",
    "санкт-петербург": "sankt-peterburg",
    "питер": "sankt-peterburg",
    "спб": "sankt-peterburg",
    "казань": "kazan",
    "новосибирск": "novosibirsk",
    "екатеринбург": "ekaterinburg",
    "краснодар": "krasnodar",
    "самара": "samara",
    "ростов-на-дону": "rostov-na-donu",
    "нижний новгород": "nizhniy_novgorod",
    "челябинск": "chelyabinsk",
    "уфа": "ufa",
    "воронеж": "voronezh",
    "пермь": "perm",
    "омск": "omsk",
    "волгоград": "volgograd",
}


def _city_to_slug(city: str) -> str:
    if not city:
        return "rossiya"
    key = city.lower().strip()
    return CITY_SLUGS.get(key, key.replace(" ", "_"))


def _group_filters(filter_rows: list) -> dict:
    groups = {}
    for user_filter, user in filter_rows:
        brands = user_filter.brands or [None]
        regions = user_filter.regions or ["moskva"]
        for brand in brands:
            for region in regions:
                key = (brand, _city_to_slug(region))
                if key not in groups:
                    groups[key] = []
                groups[key].append((user_filter, user))
    return groups


async def run_parse_cycle():
    logger.info("=== Старт цикла парсинга ===")

    async with AsyncSessionLocal() as session:
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
    search_groups = _group_filters(filter_rows)

    for search_params, filters_and_users in search_groups.items():
        try:
            await _process_search_group(search_params, filters_and_users)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Ошибка группы {search_params}: {e}")

    logger.info("=== Цикл парсинга завершён ===")


async def _process_search_group(search_params: tuple, filters_and_users: list):
    brand, region_slug = search_params
    logger.info(f"Парсим: марка={brand}, регион={region_slug}")

    all_listings = []
    for page in range(1, 4):
        listings = await parser.parse_search_page(
            brand=brand,
            region=region_slug,
            page=page
        )
        all_listings.extend(listings)
        if len(listings) < 10:
            break
        await asyncio.sleep(3)

    logger.info(f"Спарсили {len(all_listings)} объявлений")

    new_count = 0
    for listing_data in all_listings:
        try:
            is_new = await _process_listing(listing_data)
            if is_new:
                new_count += 1
        except Exception as e:
            logger.error(f"Ошибка объявления: {e}")

    logger.info(f"Новых объявлений: {new_count}")

    if new_count > 0:
        await _check_and_send_alerts(filters_and_users)


async def _process_listing(listing_data: dict) -> bool:
    avito_id = listing_data.get("avito_id")
    if not avito_id:
        return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Listing).where(Listing.avito_id == avito_id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            new_price = listing_data.get("price")
            if new_price and existing.price != new_price:
                history = PriceHistory(listing_id=existing.id, price=new_price)
                session.add(history)
                existing.price = new_price
                await session.commit()
                logger.info(f"Цена изменилась: {avito_id} → {new_price}")
            return False

        url = listing_data.get("url")
        if url:
            details = await parser.parse_listing_detail(url)
            if details:
                listing_data.update(details)

        brand = listing_data.get("brand")
        model = listing_data.get("model")

        market_price = 0
        if brand and listing_data.get("year") and listing_data.get("mileage"):
            market_price = await get_market_price(
                brand=brand,
                model=model or "",
                year=listing_data["year"],
                mileage=listing_data["mileage"],
                region=listing_data.get("location", "Россия")
            )

        price = listing_data.get("price", 0)
        price_diff_pct = None
        if market_price and price:
            price_diff_pct = ((price - market_price) / market_price) * 100

        seller_listings_count = listing_data.get("seller_listings_count", 1)
        is_reseller = (
            listing_data.get("has_dealer_keywords", False) or
            listing_data.get("seller_type") == "dealer" or
            seller_listings_count > 5
        )

        is_duplicate = False
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
            seller_name=listing_data.get("seller_name", ""),
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
        await session.flush()

        history = PriceHistory(listing_id=new_listing.id, price=price)
        session.add(history)
        await session.commit()
        listing_id = new_listing.id

    if not is_duplicate:
        await _analyze_and_update(listing_id, listing_data, market_price)

    return True


async def _analyze_and_update(listing_id: int, listing_data: dict, market_price: int):
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
                if analysis.get("seller_analysis", {}).get("is_reseller"):
                    listing.is_reseller = True
                if analysis.get("photo_analysis", {}).get("is_stock_photo"):
                    listing.is_stock_photo = True
                await session.commit()

    except Exception as e:
        logger.error(f"Ошибка AI-анализа {listing_id}: {e}")


async def _check_and_send_alerts(filters_and_users: list):
    from bot.main import send_alert

    async with AsyncSessionLocal() as session:
        for user_filter, user in filters_and_users:
            try:
                cutoff = datetime.now() - timedelta(minutes=35)
                query = select(Listing).where(
                    and_(
                        Listing.score >= max(user_filter.min_score, MIN_SCORE_FOR_ALERT),
                        Listing.is_duplicate == False,
                        Listing.analyzed_at.isnot(None),
                        Listing.parsed_at >= cutoff
                    )
                )

                if user_filter.brands:
                    query = query.where(Listing.brand.in_(user_filter.brands))
                if user_filter.price_min:
                    query = query.where(Listing.price >= user_filter.price_min)
                if user_filter.price_max:
                    query = query.where(Listing.price <= user_filter.price_max)
                if user_filter.year_min:
                    query = query.where(Listing.year >= user_filter.year_min)
                if user_filter.year_max:
                    query = query.where(Listing.year <= user_filter.year_max)

                result = await session.execute(query)
                matching_listings = result.scalars().all()

                for listing in matching_listings:
                    if user_filter.keywords_include:
                        text = f"{listing.title} {listing.description or ''}".lower()
                        if not any(kw in text for kw in user_filter.keywords_include):
                            continue

                    await send_alert(
                        user.telegram_id,
                        listing,
                        listing.ai_analysis or {}
                    )
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Ошибка алертов {user.telegram_id}: {e}")


async def start_scheduler():
    await parser.start()

    scheduler.add_job(
        run_parse_cycle,
        "interval",
        minutes=2,
        id="main_parse",
        next_run_time=datetime.now()
    )

    scheduler.start()
    logger.info("Планировщик запущен")


async def stop_scheduler():
    scheduler.shutdown()
    await parser.stop()
    logger.info("Планировщик остановлен")

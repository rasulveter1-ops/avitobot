"""
Скрипт импорта полного каталога авто с Google Drive в PostgreSQL
"""
import re
import os
import sys
import asyncio
import json
from datetime import datetime
from loguru import logger

FILE_ID = "1IAhvdr6qMX15n4L1UDBlcBPN20k2-FD_"
LOCAL_PATH = "/app/autoscan/190105.xlsx"

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

BATCH_SIZE = 500


def parse_params(params_str: str) -> dict:
    result = {}
    if not params_str or str(params_str) == 'nan':
        return result
    for item in str(params_str).split("|"):
        item = item.strip()
        if "=" in item:
            key, val = item.split("=", 1)
            result[key.strip()] = val.strip()
    return result


def extract_brand_model(title: str) -> tuple:
    brands = [
        "Toyota", "Honda", "Kia", "Hyundai", "Nissan", "Mazda",
        "BMW", "Mercedes-Benz", "Audi", "Volkswagen", "Skoda",
        "Lada", "ВАЗ", "Renault", "Ford", "Chevrolet", "Lexus",
        "Infiniti", "Subaru", "Mitsubishi", "Suzuki", "Volvo",
        "Jeep", "Land Rover", "Porsche", "Chery", "Geely", "Haval",
        "Exeed", "Omoda", "Tank", "Jaecoo", "Voyah", "Zeekr",
        "GAC", "Wey", "Changan", "BYD", "Opel", "Peugeot",
        "Citroen", "Fiat", "Bentley", "Cadillac", "Dodge",
        "Acura", "Buick", "Alfa Romeo", "Seat", "Great Wall",
        "УАЗ", "ГАЗ", "ИЖ", "Москвич", "Daewoo", "Ravon",
    ]
    title_lower = str(title).lower()
    for brand in brands:
        if brand.lower() in title_lower:
            idx = title_lower.index(brand.lower()) + len(brand)
            rest = title[idx:].strip().split()
            model = rest[0] if rest else None
            return brand, model
    return None, None


def extract_year(params: dict, title: str):
    year_raw = params.get("Год выпуска", "")
    if year_raw:
        try:
            y = int(re.sub(r"[^\d]", "", str(year_raw))[:4])
            if 1900 <= y <= 2030:
                return y
        except:
            pass
    m = re.search(r"\b(19|20)\d{2}\b", str(title))
    if m:
        return int(m.group())
    return None


def extract_mileage(params: dict, title: str):
    mileage_raw = params.get("Пробег", "")
    if mileage_raw:
        clean = re.sub(r"[^\d]", "", str(mileage_raw).split("км")[0].split("-")[0])
        if clean:
            try:
                return int(clean)
            except:
                pass
    m = re.search(r"([\d\s]+)\s*км", str(title))
    if m:
        clean = re.sub(r"\s", "", m.group(1))
        if clean:
            try:
                return int(clean)
            except:
                pass
    return None


def process_row(row) -> dict | None:
    try:
        avito_id = str(row.get("uID", "")).strip()
        if not avito_id or avito_id == 'nan':
            return None

        params = parse_params(row.get("Параметры", ""))
        title = str(row.get("Название", "")).strip()
        brand, model = extract_brand_model(title)
        year = extract_year(params, title)
        mileage = extract_mileage(params, title)

        price = 0
        try:
            price = int(str(row.get("Цена", 0)).replace(" ", "").replace("₽", ""))
        except:
            pass

        user_type = str(row.get("Тип пользователя", "")).lower()
        seller_type = "dealer" if ("дилер" in user_type or "компания" in user_type) else "private"

        desc = str(row.get("Описание", ""))
        if desc == 'nan':
            desc = ""
        full_text = f"{title} {desc}".lower()
        urgent_keywords = [kw for kw in [
            "срочно", "торг уместен", "торг при осмотре",
            "обмен", "уезжаю", "переезд", "нужны деньги"
        ] if kw in full_text]
        is_urgent = len(urgent_keywords) > 0

        published_at = None
        try:
            published_at = datetime.fromisoformat(str(row.get("Дата", "")))
        except:
            pass

        lon = None
        lat = None
        try:
            lon_val = row.get("Долгота", "")
            lat_val = row.get("Широта", "")
            if str(lon_val) != 'nan':
                lon = float(lon_val)
            if str(lat_val) != 'nan':
                lat = float(lat_val)
        except:
            pass

        photos = str(row.get("Ссылки на картинки", ""))
        if photos == 'nan':
            photos = ""

        owners_raw = re.sub(r"[^\d]", "", str(params.get("Владельцев по ПТС", "0")))
        owners = int(owners_raw) if owners_raw else 0

        def safe(val, max_len=None):
            s = str(val) if val is not None and str(val) != 'nan' else None
            if s and max_len:
                s = s[:max_len]
            return s

        return {
            "avito_id": avito_id,
            "published_at": published_at,
            "title": safe(title, 500),
            "price": price,
            "brand": safe(brand, 100),
            "model": safe(model, 100),
            "year": year,
            "mileage": mileage,
            "engine_volume": safe(params.get("Объм двигателя"), 20),
            "transmission": safe(params.get("Коробка передач"), 50),
            "body_type": safe(params.get("Тип кузова"), 50),
            "color": safe(params.get("Цвет"), 50),
            "condition": safe(params.get("Состояние"), 50),
            "owners_count": owners,
            "pts": safe(params.get("ПТС"), 50),
            "exchange": "обмен" in str(params.get("Обмен", "")).lower(),
            "seller_name": safe(row.get("Контактное лицо"), 200),
            "seller_type": seller_type,
            "region": safe(row.get("Регион"), 200),
            "city": safe(row.get("Город"), 100),
            "district": safe(row.get("Район"), 100),
            "address": safe(row.get("Адрес"), 300),
            "description": desc[:2000] if desc else None,
            "url": safe(row.get("Ссылка на объявление"), 500),
            "photos": photos[:2000] if photos else None,
            "is_urgent": is_urgent,
            "urgent_keywords": json.dumps(urgent_keywords, ensure_ascii=False),
            "longitude": lon,
            "latitude": lat,
        }
    except Exception as e:
        logger.error(f"Ошибка строки: {e}")
        return None


async def create_tables(engine):
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS catalog (
                id SERIAL PRIMARY KEY,
                avito_id VARCHAR(50) UNIQUE NOT NULL,
                published_at TIMESTAMP,
                title VARCHAR(500),
                price INTEGER,
                brand VARCHAR(100),
                model VARCHAR(100),
                year INTEGER,
                mileage INTEGER,
                engine_volume VARCHAR(20),
                transmission VARCHAR(50),
                body_type VARCHAR(50),
                color VARCHAR(50),
                condition VARCHAR(50),
                owners_count INTEGER DEFAULT 0,
                pts VARCHAR(50),
                exchange BOOLEAN DEFAULT FALSE,
                seller_name VARCHAR(200),
                seller_type VARCHAR(20) DEFAULT 'private',
                region VARCHAR(200),
                city VARCHAR(100),
                district VARCHAR(100),
                address VARCHAR(300),
                description TEXT,
                url VARCHAR(500),
                photos TEXT,
                is_urgent BOOLEAN DEFAULT FALSE,
                urgent_keywords TEXT,
                longitude FLOAT,
                latitude FLOAT,
                imported_at TIMESTAMP DEFAULT NOW()
            )
        """))
        for idx in ["brand", "price", "year", "city", "mileage", "seller_type"]:
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS idx_catalog_{idx} ON catalog({idx})"
            ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS market_stats (
                id SERIAL PRIMARY KEY,
                brand VARCHAR(100),
                model VARCHAR(100),
                year INTEGER,
                city VARCHAR(100),
                avg_price INTEGER,
                min_price INTEGER,
                max_price INTEGER,
                count INTEGER,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
    logger.info("✅ Таблицы и индексы готовы")


async def download_from_gdrive(file_id: str, output_path: str):
    import httpx
    logger.info("Скачиваем файл с Google Drive...")

    session_url = f"https://drive.google.com/uc?export=download&id={file_id}"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0),
        follow_redirects=True
    ) as client:
        # Первый запрос
        resp = await client.get(session_url)
        logger.info(f"Первый запрос: статус {resp.status_code}, размер {len(resp.content)} байт")

        # Ищем confirm token
        confirm = None
        content_str = resp.content.decode('utf-8', errors='ignore')

        patterns = [
            r'confirm=([0-9A-Za-z_\-]+)',
            r'"confirm","([^"]+)"',
            r'name="confirm"\s+value="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, content_str)
            if match:
                confirm = match.group(1)
                logger.info(f"Найден confirm token: {confirm}")
                break

        if confirm:
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={confirm}"
        else:
            # Пробуем альтернативный URL
            download_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            logger.info("Confirm не найден, пробуем usercontent URL")

        # Скачиваем файл
        logger.info(f"Скачиваем: {download_url[:80]}...")
        total_size = 0

        async with client.stream("GET", download_url) as stream_resp:
            logger.info(f"Статус скачивания: {stream_resp.status_code}")
            if stream_resp.status_code not in [200, 206]:
                raise Exception(f"Ошибка скачивания: {stream_resp.status_code}")

            with open(output_path, 'wb') as f:
                async for chunk in stream_resp.aiter_bytes(chunk_size=1024*1024):
                    f.write(chunk)
                    total_size += len(chunk)
                    if total_size % (100*1024*1024) == 0:
                        logger.info(f"Скачано: {total_size // (1024*1024)} MB")

    size_mb = total_size // (1024*1024)
    logger.info(f"✅ Файл скачан: {size_mb} MB")

    if size_mb < 10:
        raise Exception(f"Файл слишком маленький ({size_mb} MB) — возможно скачалась HTML страница")


async def import_file(filepath: str, engine):
    import pandas as pd
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import text

    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    logger.info(f"Читаем Excel файл... (может занять 3-5 минут для 430к строк)")
    df_full = pd.read_excel(filepath, engine='openpyxl')
    total_rows = len(df_full)
    logger.info(f"✅ Файл прочитан: {total_rows:,} строк".replace(",", " "))

    success = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        for chunk_start in range(0, total_rows, BATCH_SIZE):
            chunk = df_full[chunk_start:chunk_start + BATCH_SIZE]

            for _, row in chunk.iterrows():
                processed = process_row(row.to_dict())
                if not processed:
                    skipped += 1
                    continue
                try:
                    await session.execute(text("""
                        INSERT INTO catalog
                        (avito_id, published_at, title, price, brand, model, year,
                         mileage, engine_volume, transmission, body_type, color,
                         condition, owners_count, pts, exchange, seller_name,
                         seller_type, region, city, district, address, description,
                         url, photos, is_urgent, urgent_keywords, longitude, latitude)
                        VALUES
                        (:avito_id, :published_at, :title, :price, :brand, :model, :year,
                         :mileage, :engine_volume, :transmission, :body_type, :color,
                         :condition, :owners_count, :pts, :exchange, :seller_name,
                         :seller_type, :region, :city, :district, :address, :description,
                         :url, :photos, :is_urgent, :urgent_keywords, :longitude, :latitude)
                        ON CONFLICT (avito_id) DO UPDATE SET
                            price = EXCLUDED.price,
                            published_at = EXCLUDED.published_at
                    """), processed)
                    success += 1
                except Exception as e:
                    skipped += 1

            await session.commit()

            if chunk_start % 10000 == 0:
                pct = int(chunk_start / total_rows * 100)
                logger.info(f"Прогресс: {pct}% ({chunk_start:,}/{total_rows:,})".replace(",", " "))

    logger.info(f"✅ Импорт завершён: {success:,} загружено, {skipped:,} пропущено".replace(",", " "))

    # Статистика рынка
    logger.info("Считаем статистику рынка...")
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE market_stats"))
        await session.execute(text("""
            INSERT INTO market_stats (brand, model, year, city, avg_price, min_price, max_price, count)
            SELECT brand, model, year, city,
                   AVG(price)::INTEGER, MIN(price), MAX(price), COUNT(*)
            FROM catalog
            WHERE brand IS NOT NULL AND year IS NOT NULL
              AND price > 50000 AND price < 50000000
            GROUP BY brand, model, year, city
            HAVING COUNT(*) >= 2
        """))
        await session.commit()

    # Финальная статистика
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT COUNT(*), COUNT(DISTINCT brand), COUNT(DISTINCT city),
                   AVG(price)::INTEGER, MIN(price), MAX(price),
                   COUNT(CASE WHEN seller_type='private' THEN 1 END),
                   COUNT(CASE WHEN is_urgent THEN 1 END)
            FROM catalog
        """))
        s = result.fetchone()
        logger.info(f"""
╔══════════════════════════════════════╗
║      📊 КАТАЛОГ ЗАГРУЖЕН             ║
╠══════════════════════════════════════╣
║ Объявлений:  {s[0]:>12,}             ║
║ Марок:       {s[1]:>12,}             ║
║ Городов:     {s[2]:>12,}             ║
║ Средняя цена:{s[3]:>10,}₽            ║
║ Мин цена:    {s[4]:>10,}₽            ║
║ Макс цена:   {s[5]:>10,}₽            ║
║ Частников:   {s[6]:>12,}             ║
║ Срочных:     {s[7]:>12,}             ║
╚══════════════════════════════════════╝
        """.replace(",", " "))


    # Создаем папку для файла
    os.makedirs("/app/autoscan", exist_ok=True)

    # Если файла нет в контейнере — скачиваем с Google Drive
    if not os.path.exists(LOCAL_PATH):
        logger.warning("Файл не найден локально. Скачиваем с Google Drive...")

        await download_from_gdrive(
            FILE_ID,
            LOCAL_PATH
        )

    size_mb = os.path.getsize(LOCAL_PATH) // (1024 * 1024)

    if size_mb < 10:
        raise Exception(
            f"Файл слишком маленький: {size_mb} MB. Возможно, скачалась HTML-страница, а не Excel."
        )

    logger.info(f"Файл найден: {size_mb} MB")

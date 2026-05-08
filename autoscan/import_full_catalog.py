"""
Скрипт импорта полного каталога авто с Google Drive в PostgreSQL
Запуск: python import_full_catalog.py
"""
import re
import os
import sys
import asyncio
import gzip
import json
from datetime import datetime
from loguru import logger

# URL файла на Google Drive
GDRIVE_URL = "https://drive.google.com/uc?export=download&id=1IAhvdr6qMX15n4L1UDBlcBPN20k2-FD_&confirm=t"

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

BATCH_SIZE = 500  # строк за один INSERT


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


def extract_year(params: dict, title: str) -> int | None:
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


def extract_mileage(params: dict, title: str) -> int | None:
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
    """Обработка одной строки DataFrame"""
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

        desc = str(row.get("Описание", "")) if str(row.get("Описание", "")) != 'nan' else ""
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

        return {
            "avito_id": avito_id,
            "published_at": published_at,
            "title": title,
            "price": price,
            "brand": brand,
            "model": model,
            "year": year,
            "mileage": mileage,
            "engine_volume": params.get("Объм двигателя", "")[:20] if params.get("Объм двигателя") else None,
            "transmission": params.get("Коробка передач", "")[:50] if params.get("Коробка передач") else None,
            "body_type": params.get("Тип кузова", "")[:50] if params.get("Тип кузова") else None,
            "color": params.get("Цвет", "")[:50] if params.get("Цвет") else None,
            "condition": params.get("Состояние", "")[:50] if params.get("Состояние") else None,
            "owners_count": owners,
            "pts": params.get("ПТС", "")[:50] if params.get("ПТС") else None,
            "exchange": "обмен" in str(params.get("Обмен", "")).lower(),
            "seller_name": str(row.get("Контактное лицо", ""))[:200] if str(row.get("Контактное лицо", "")) != 'nan' else None,
            "seller_type": seller_type,
            "region": str(row.get("Регион", ""))[:200] if str(row.get("Регион", "")) != 'nan' else None,
            "city": str(row.get("Город", ""))[:100] if str(row.get("Город", "")) != 'nan' else None,
            "district": str(row.get("Район", ""))[:100] if str(row.get("Район", "")) != 'nan' else None,
            "address": str(row.get("Адрес", ""))[:300] if str(row.get("Адрес", "")) != 'nan' else None,
            "description": desc[:2000] if desc else None,
            "url": str(row.get("Ссылка на объявление", ""))[:500] if str(row.get("Ссылка на объявление", "")) != 'nan' else None,
            "photos": photos[:2000] if photos else None,
            "is_urgent": is_urgent,
            "urgent_keywords": json.dumps(urgent_keywords, ensure_ascii=False),
            "longitude": lon,
            "latitude": lat,
        }
    except Exception as e:
        return None


async def create_table(engine):
    """Создание таблицы каталога"""
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

        # Индексы для быстрого поиска
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_catalog_brand ON catalog(brand)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_catalog_price ON catalog(price)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_catalog_year ON catalog(year)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_catalog_city ON catalog(city)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_catalog_mileage ON catalog(mileage)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_catalog_seller_type ON catalog(seller_type)"))

    logger.info("✅ Таблица catalog и индексы готовы")


async def compute_market_stats(engine):
    """Подсчёт статистики рынка после импорта"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS market_stats (
                id SERIAL PRIMARY KEY,
                brand VARCHAR(100),
                model VARCHAR(100),
                year INTEGER,
                region VARCHAR(200),
                avg_price INTEGER,
                min_price INTEGER,
                max_price INTEGER,
                count INTEGER,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))

        await conn.execute(text("TRUNCATE market_stats"))

        await conn.execute(text("""
            INSERT INTO market_stats (brand, model, year, region, avg_price, min_price, max_price, count)
            SELECT
                brand,
                model,
                year,
                city as region,
                AVG(price)::INTEGER as avg_price,
                MIN(price) as min_price,
                MAX(price) as max_price,
                COUNT(*) as count
            FROM catalog
            WHERE brand IS NOT NULL
              AND year IS NOT NULL
              AND price > 50000
              AND price < 50000000
            GROUP BY brand, model, year, city
            HAVING COUNT(*) >= 2
        """))

    logger.info("✅ Статистика рынка посчитана")


async def import_file(filepath: str):
    """Основная функция импорта"""
    import pandas as pd
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text

    engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await create_table(engine)

    logger.info(f"Читаем файл: {filepath}")
    logger.info("Это может занять несколько минут для большого файла...")

    # Читаем файл чанками
    if filepath.endswith('.csv'):
        reader = pd.read_csv(filepath, encoding='utf-8', sep=';',
                            on_bad_lines='skip', chunksize=BATCH_SIZE)
    else:
        # Excel — читаем целиком но обрабатываем батчами
        logger.info("Читаем Excel файл... (может занять 2-5 минут)")
        df_full = pd.read_excel(filepath, engine='openpyxl')
        logger.info(f"Файл прочитан: {len(df_full)} строк")
        # Создаём итератор чанков
        chunks = [df_full[i:i+BATCH_SIZE] for i in range(0, len(df_full), BATCH_SIZE)]
        reader = chunks

    total = 0
    success = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        for chunk_num, chunk in enumerate(reader):
            rows_to_insert = []

            for _, row in chunk.iterrows():
                processed = process_row(row.to_dict())
                if processed:
                    rows_to_insert.append(processed)
                else:
                    skipped += 1

            if rows_to_insert:
                for row_data in rows_to_insert:
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
                        """), row_data)
                        success += 1
                    except Exception as e:
                        skipped += 1

                await session.commit()

            total += len(chunk)
            if chunk_num % 10 == 0:
                logger.info(f"Прогресс: {total} строк обработано, {success} загружено")

    logger.info(f"✅ Импорт завершён!")
    logger.info(f"   Всего строк: {total}")
    logger.info(f"   Загружено: {success}")
    logger.info(f"   Пропущено: {skipped}")

    # Считаем статистику
    logger.info("Считаем статистику рынка...")
    await compute_market_stats(engine)

    # Финальная статистика
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(DISTINCT brand) as brands,
                COUNT(DISTINCT city) as cities,
                AVG(price)::INTEGER as avg_price,
                MIN(price) as min_price,
                MAX(price) as max_price,
                COUNT(CASE WHEN seller_type = 'private' THEN 1 END) as private_count,
                COUNT(CASE WHEN is_urgent THEN 1 END) as urgent_count
            FROM catalog
        """))
        s = result.fetchone()
        logger.info(f"""
╔══════════════════════════════════════╗
║      📊 КАТАЛОГ ЗАГРУЖЕН             ║
╠══════════════════════════════════════╣
║ Объявлений:    {s[0]:>10,}           ║
║ Марок:         {s[1]:>10,}           ║
║ Городов:       {s[2]:>10,}           ║
║ Средняя цена:  {s[3]:>10,}₽          ║
║ Мин цена:      {s[4]:>10,}₽          ║
║ Макс цена:     {s[5]:>10,}₽          ║
║ Частников:     {s[6]:>10,}           ║
║ Срочных:       {s[7]:>10,}           ║
╚══════════════════════════════════════╝
        """.replace(",", " "))

    await engine.dispose()


async def download_from_gdrive(file_id: str, output_path: str):
    """Скачивание большого файла с Google Drive с подтверждением"""
    import httpx
    logger.info("Скачиваем файл с Google Drive...")

    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        # Первый запрос — получаем confirm token
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        resp = await client.get(url)
        
        # Ищем confirm token в ответе
        confirm = None
        if b"confirm=" in resp.content:
            import re
            match = re.search(rb'confirm=([0-9A-Za-z_\-]+)', resp.content)
            if match:
                confirm = match.group(1).decode()
        
        # Второй запрос с confirm token
        if confirm:
            url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={confirm}"
            resp = await client.get(url)
        
        if resp.status_code != 200:
            raise Exception(f"Ошибка: {resp.status_code}")
        
        with open(output_path, 'wb') as f:
            f.write(resp.content)
        
        size_mb = len(resp.content) // (1024*1024)
        logger.info(f"✅ Файл скачан: {size_mb} MB")(url: str, output_path: str):
    """Скачивание файла с Google Drive"""
    import httpx
    logger.info(f"Скачиваем файл с Google Drive...")
    logger.info("Файл большой (~700MB), это займёт несколько минут...")

    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise Exception(f"Ошибка скачивания: {resp.status_code}")

            total_size = 0
            with open(output_path, 'wb') as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024*1024):
                    f.write(chunk)
                    total_size += len(chunk)
                    if total_size % (50*1024*1024) == 0:
                        logger.info(f"Скачано: {total_size // (1024*1024)} MB")

    logger.info(f"✅ Файл скачан: {total_size // (1024*1024)} MB")
    return output_path


async def main():
    # Устанавливаем зависимости
    import subprocess
    logger.info("Устанавливаем зависимости...")
    subprocess.run(["pip", "install", "openpyxl", "pandas", "httpx", "--break-system-packages", "-q"])

    filepath = "/tmp/catalog.xlsx"

    # Скачиваем файл
    if not os.path.exists(filepath):
        await download_from_gdrive(GDRIVE_URL, filepath)
    else:
        logger.info(f"Файл уже скачан: {filepath}")

    # Импортируем
    await import_file(filepath)


if __name__ == "__main__":
    asyncio.run(main())

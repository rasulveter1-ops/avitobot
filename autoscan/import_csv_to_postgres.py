import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://postgres:SHpxDdSumTlEbBIAazWOFEMTFggjCNCN@shortline.proxy.rlwy.net:13648/railway"

CSV_PATH = "190105.csv"
BATCH_SIZE = 100

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300
)


def create_tables():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS catalog (
                id SERIAL PRIMARY KEY,
                avito_id TEXT UNIQUE,
                title TEXT,
                price BIGINT,
                brand TEXT,
                model TEXT,
                year INTEGER,
                mileage INTEGER,
                city TEXT,
                region TEXT,
                seller_type TEXT,
                url TEXT,
                imported_at TIMESTAMP DEFAULT NOW()
            )
        """))

    print("Таблица готова")


def process_row(row):
    return {
        "avito_id": str(row.get("uID", "")),
        "title": str(row.get("Название", "")),
        "price": 0,
        "brand": None,
        "model": None,
        "year": None,
        "mileage": None,
        "city": str(row.get("Город", "")),
        "region": str(row.get("Регион", "")),
        "seller_type": str(row.get("Тип пользователя", "")),
        "url": str(row.get("Ссылка на объявление", ""))
    }


def import_csv():
    print("Начинаю читать CSV...")

    total = 0

    insert_sql = text("""
        INSERT INTO catalog (
            avito_id,
            title,
            price,
            brand,
            model,
            year,
            mileage,
            city,
            region,
            seller_type,
            url
        )
        VALUES (
            :avito_id,
            :title,
            :price,
            :brand,
            :model,
            :year,
            :mileage,
            :city,
            :region,
            :seller_type,
            :url
        )
        ON CONFLICT (avito_id)
        DO NOTHING
    """)

    for chunk in pd.read_csv(
        CSV_PATH,
        chunksize=BATCH_SIZE,
        low_memory=False
    ):
        rows = []

        for _, row in chunk.iterrows():
            rows.append(process_row(row.to_dict()))
            total += 1

        print(f"Пишем batch: {len(rows)}")

        try:
            with engine.begin() as conn:
                conn.execute(insert_sql, rows)

            print(f"Импортировано: {total}")

        except Exception as e:
            print("Ошибка при записи batch:")
            print(e)
            print("Пробуем продолжить со следующим batch...")


if __name__ == "__main__":
    create_tables()
    import_csv()
    print("ГОТОВО")
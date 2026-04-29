from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, JSON, Text, ForeignKey, BigInteger
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class User(Base):
    """Пользователь сервиса"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(100), nullable=True)
    full_name = Column(String(200), nullable=True)
    plan = Column(String(20), default="free")  # free, basic, pro, expert
    is_active = Column(Boolean, default=True)
    subscription_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    filters = relationship("UserFilter", back_populates="user")
    saved_listings = relationship("SavedListing", back_populates="user")


class UserFilter(Base):
    """Фильтры пользователя для поиска"""
    __tablename__ = "user_filters"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), default="Мой фильтр")
    is_active = Column(Boolean, default=True)

    # Параметры фильтра
    brands = Column(JSON, default=list)        # ["Toyota", "Honda"]
    models = Column(JSON, default=list)        # ["Camry", "CR-V"]
    price_min = Column(Integer, nullable=True)
    price_max = Column(Integer, nullable=True)
    year_min = Column(Integer, nullable=True)
    year_max = Column(Integer, nullable=True)
    mileage_max = Column(Integer, nullable=True)
    regions = Column(JSON, default=list)       # ["Москва", "МО"]
    radius_km = Column(Integer, nullable=True) # радиус от города
    seller_type = Column(String(20), default="any")  # any, private, dealer

    # Ключевые слова
    keywords_include = Column(JSON, default=list)  # ["срочно", "обмен"]
    keywords_exclude = Column(JSON, default=list)  # ["автосалон", "дилер"]

    # Минимальный скоринг для алерта
    min_score = Column(Float, default=6.0)

    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="filters")


class Listing(Base):
    """Объявление с Авито"""
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True)
    avito_id = Column(String(50), unique=True, nullable=False)
    url = Column(String(500), nullable=False)

    # Основные данные
    title = Column(String(500), nullable=False)
    price = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)

    # Характеристики авто
    brand = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    year = Column(Integer, nullable=True)
    mileage = Column(Integer, nullable=True)
    color = Column(String(50), nullable=True)
    body_type = Column(String(50), nullable=True)
    transmission = Column(String(50), nullable=True)
    engine_volume = Column(Float, nullable=True)

    # Продавец
    seller_name = Column(String(200), nullable=True)
    seller_type = Column(String(20), nullable=True)  # private, dealer
    seller_avito_id = Column(String(50), nullable=True)
    seller_listings_count = Column(Integer, nullable=True)

    # Локация
    region = Column(String(200), nullable=True)
    city = Column(String(100), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Фото
    photos = Column(JSON, default=list)  # список URL фотографий

    # Статусы
    is_active = Column(Boolean, default=True)
    published_at = Column(DateTime, nullable=True)
    parsed_at = Column(DateTime, server_default=func.now())
    analyzed_at = Column(DateTime, nullable=True)

    # AI анализ
    score = Column(Float, nullable=True)           # общий скоринг 1-10
    ai_analysis = Column(JSON, nullable=True)      # полный анализ от Claude
    market_price = Column(Integer, nullable=True)  # рыночная цена аналогов
    price_diff_pct = Column(Float, nullable=True)  # % отклонения от рынка

    # Детекторы
    is_urgent = Column(Boolean, default=False)
    is_reseller = Column(Boolean, default=False)
    is_stock_photo = Column(Boolean, default=False)
    is_duplicate = Column(Boolean, default=False)
    urgent_keywords = Column(JSON, default=list)

    price_history = relationship("PriceHistory", back_populates="listing")


class PriceHistory(Base):
    """История изменения цены объявления"""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    price = Column(Integer, nullable=False)
    recorded_at = Column(DateTime, server_default=func.now())

    listing = relationship("Listing", back_populates="price_history")


class SavedListing(Base):
    """Сохранённые объявления пользователя"""
    __tablename__ = "saved_listings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    notes = Column(Text, nullable=True)
    saved_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="saved_listings")


class Deal(Base):
    """Портфель сделок пользователя"""
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=True)

    # Данные сделки
    car_title = Column(String(300), nullable=False)
    buy_price = Column(Integer, nullable=False)
    repair_cost = Column(Integer, default=0)
    sell_price = Column(Integer, nullable=True)

    bought_at = Column(DateTime, nullable=True)
    sold_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="active")  # active, sold

    @property
    def profit(self):
        if self.sell_price:
            return self.sell_price - self.buy_price - self.repair_cost
        return None

    @property
    def days_to_sell(self):
        if self.bought_at and self.sold_at:
            return (self.sold_at - self.bought_at).days
        return None


class MarketStats(Base):
    """Статистика рынка по моделям"""
    __tablename__ = "market_stats"

    id = Column(Integer, primary_key=True)
    brand = Column(String(100), nullable=False)
    model = Column(String(100), nullable=False)
    year = Column(Integer, nullable=False)
    region = Column(String(200), nullable=True)

    avg_price = Column(Integer, nullable=False)
    min_price = Column(Integer, nullable=True)
    max_price = Column(Integer, nullable=True)
    listings_count = Column(Integer, default=0)
    avg_days_on_market = Column(Float, nullable=True)

    recorded_at = Column(DateTime, server_default=func.now())

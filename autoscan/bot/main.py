import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

bot = Bot(token=BOT_TOKEN)
storage = RedisStorage.from_url(REDIS_URL)
dp = Dispatcher(storage=storage)


# ============ FSM States ============

class FilterSetup(StatesGroup):
    choosing_brands = State()
    choosing_price_min = State()
    choosing_price_max = State()
    choosing_year_min = State()
    choosing_year_max = State()
    choosing_region = State()
    choosing_radius = State()
    choosing_keywords = State()


class AdvisorState(StatesGroup):
    waiting_question = State()


# ============ Keyboards ============

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Мои фильтры"), KeyboardButton(text="💾 Сохранённые")],
            [KeyboardButton(text="📊 Аналитика рынка"), KeyboardButton(text="💼 Мои сделки")],
            [KeyboardButton(text="🤖 AI-советник"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )


def filter_actions_keyboard(filter_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_filter:{filter_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_filter:{filter_id}")
        ],
        [InlineKeyboardButton(text="➕ Новый фильтр", callback_data="new_filter")]
    ])


def listing_actions_keyboard(listing_id: int, avito_url: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть на Авито", url=avito_url)],
        [
            InlineKeyboardButton(text="💾 Сохранить", callback_data=f"save:{listing_id}"),
            InlineKeyboardButton(text="🔍 Подробнее", callback_data=f"detail:{listing_id}")
        ],
        [
            InlineKeyboardButton(text="💬 Скрипт торга", callback_data=f"script:{listing_id}"),
            InlineKeyboardButton(text="🤖 AI-разбор", callback_data=f"analyze:{listing_id}")
        ]
    ])


# ============ Handlers ============

@dp.message(CommandStart())
async def start_handler(message: Message):
    """Приветствие при старте"""
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "🚗 Я <b>AutoScan</b> — твой AI-помощник для поиска выгодных автомобилей.\n\n"
        "Я слежу за Авито 24/7, анализирую каждое объявление и присылаю только "
        "те машины где можно заработать.\n\n"
        "📌 <b>Что умею:</b>\n"
        "• Нахожу авто ниже рынка на 15-30%\n"
        "• Анализирую фото на дефекты\n"
        "• Определяю срочных продавцов\n"
        "• Считаю потенциальную прибыль\n"
        "• Даю скрипты для торга\n\n"
        "Начни с настройки фильтра 👇",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )
    
    # Регистрируем пользователя
    from database.connection import AsyncSessionLocal
    from database.models import User
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                plan="free"
            )
            session.add(user)
            await session.commit()
            logger.info(f"Новый пользователь: {message.from_user.id}")


@dp.message(F.text == "🔍 Мои фильтры")
async def my_filters_handler(message: Message):
    """Показ фильтров пользователя"""
    from database.connection import AsyncSessionLocal
    from database.models import User, UserFilter
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("Сначала запустите бота командой /start")
            return
        
        filters_result = await session.execute(
            select(UserFilter).where(UserFilter.user_id == user.id)
        )
        filters = filters_result.scalars().all()
    
    if not filters:
        await message.answer(
            "У вас пока нет фильтров.\n\n"
            "Настройте фильтр чтобы получать алерты о выгодных машинах.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать фильтр", callback_data="new_filter")]
            ])
        )
        return
    
    for f in filters:
        brands = ", ".join(f.brands) if f.brands else "Все марки"
        price_range = f"от {f.price_min:,} до {f.price_max:,} ₽".replace(",", " ") if f.price_min or f.price_max else "Любая цена"
        status = "✅ Активен" if f.is_active else "⏸ Приостановлен"
        
        text = (
            f"📋 <b>{f.name}</b> {status}\n\n"
            f"🚗 Марки: {brands}\n"
            f"💰 Цена: {price_range}\n"
            f"📍 Регион: {', '.join(f.regions) if f.regions else 'Вся Россия'}\n"
            f"📅 Год: от {f.year_min or '—'} до {f.year_max or '—'}\n"
            f"🔑 Ключевые слова: {', '.join(f.keywords_include) if f.keywords_include else 'не заданы'}"
        )
        
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=filter_actions_keyboard(f.id)
        )


@dp.callback_query(F.data == "new_filter")
async def new_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Начало создания нового фильтра"""
    await callback.message.answer(
        "🚗 <b>Создаём новый фильтр</b>\n\n"
        "Напиши марки автомобилей через запятую.\n"
        "Например: <i>Toyota, Honda, Kia</i>\n\n"
        "Или напиши <b>все</b> чтобы искать любые марки.",
        parse_mode="HTML"
    )
    await state.set_state(FilterSetup.choosing_brands)
    await callback.answer()


@dp.message(FilterSetup.choosing_brands)
async def filter_brands_handler(message: Message, state: FSMContext):
    """Обработка выбора марок"""
    text = message.text.strip()
    
    if text.lower() == "все":
        brands = []
    else:
        brands = [b.strip() for b in text.split(",") if b.strip()]
    
    await state.update_data(brands=brands)
    
    await message.answer(
        "💰 Минимальная цена (в рублях)?\n\n"
        "Например: <i>500000</i>\n"
        "Или напиши <b>нет</b> чтобы пропустить.",
        parse_mode="HTML"
    )
    await state.set_state(FilterSetup.choosing_price_min)


@dp.message(FilterSetup.choosing_price_min)
async def filter_price_min_handler(message: Message, state: FSMContext):
    text = message.text.strip()
    price_min = None
    
    if text.lower() != "нет":
        try:
            price_min = int(text.replace(" ", ""))
        except ValueError:
            await message.answer("Введите число или 'нет'")
            return
    
    await state.update_data(price_min=price_min)
    await message.answer(
        "💰 Максимальная цена (в рублях)?\n\n"
        "Например: <i>1500000</i>\n"
        "Или напиши <b>нет</b>.",
        parse_mode="HTML"
    )
    await state.set_state(FilterSetup.choosing_price_max)


@dp.message(FilterSetup.choosing_price_max)
async def filter_price_max_handler(message: Message, state: FSMContext):
    text = message.text.strip()
    price_max = None
    
    if text.lower() != "нет":
        try:
            price_max = int(text.replace(" ", ""))
        except ValueError:
            await message.answer("Введите число или 'нет'")
            return
    
    await state.update_data(price_max=price_max)
    await message.answer(
        "📍 В каком регионе искать?\n\n"
        "Например: <i>Москва</i>, <i>Санкт-Петербург</i>, <i>Казань</i>\n"
        "Или напиши <b>вся Россия</b>.",
        parse_mode="HTML"
    )
    await state.set_state(FilterSetup.choosing_region)


@dp.message(FilterSetup.choosing_region)
async def filter_region_handler(message: Message, state: FSMContext):
    text = message.text.strip()
    
    if text.lower() == "вся россия":
        regions = []
    else:
        regions = [r.strip() for r in text.split(",")]
    
    await state.update_data(regions=regions)
    await message.answer(
        "🔑 Ключевые слова для поиска?\n\n"
        "Например: <i>срочно, обмен, торг</i>\n"
        "Или напиши <b>нет</b> чтобы пропустить.",
        parse_mode="HTML"
    )
    await state.set_state(FilterSetup.choosing_keywords)


@dp.message(FilterSetup.choosing_keywords)
async def filter_keywords_handler(message: Message, state: FSMContext):
    text = message.text.strip()
    
    if text.lower() == "нет":
        keywords = []
    else:
        keywords = [k.strip().lower() for k in text.split(",")]
    
    await state.update_data(keywords=keywords)
    
    # Сохраняем фильтр
    data = await state.get_data()
    await state.clear()
    
    from database.connection import AsyncSessionLocal
    from database.models import User, UserFilter
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        
        if user:
            new_filter = UserFilter(
                user_id=user.id,
                name=f"Фильтр {len(data.get('brands', []) or ['Все'])}",
                brands=data.get("brands", []),
                price_min=data.get("price_min"),
                price_max=data.get("price_max"),
                regions=data.get("regions", []),
                keywords_include=data.get("keywords", [])
            )
            session.add(new_filter)
            await session.commit()
    
    brands_text = ", ".join(data.get("brands") or ["Все марки"])
    price_text = ""
    if data.get("price_min"):
        price_text += f"от {data['price_min']:,} ".replace(",", " ")
    if data.get("price_max"):
        price_text += f"до {data['price_max']:,} ".replace(",", " ")
    price_text = price_text.strip() + " ₽" if price_text else "Любая"
    
    await message.answer(
        "✅ <b>Фильтр создан!</b>\n\n"
        f"🚗 Марки: {brands_text}\n"
        f"💰 Цена: {price_text}\n"
        f"📍 Регион: {', '.join(data.get('regions') or ['Вся Россия'])}\n"
        f"🔑 Ключевые слова: {', '.join(data.get('keywords') or ['не заданы'])}\n\n"
        "Я начну присылать алерты как только найду подходящие машины! 🔔",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


@dp.message(F.text == "🤖 AI-советник")
async def advisor_handler(message: Message, state: FSMContext):
    """Запуск AI-советника"""
    await message.answer(
        "🤖 <b>AI-советник</b>\n\n"
        "Задай любой вопрос об авторынке:\n\n"
        "• <i>Стоит ли брать Camry 2018 за 1.2 млн?</i>\n"
        "• <i>Где выгоднее купить Honda CR-V?</i>\n"
        "• <i>Какой пробег нормальный для Kia Rio 2017?</i>\n"
        "• <i>Найди Camry до 1 млн с потенциалом +100к</i>\n\n"
        "Напиши свой вопрос 👇",
        parse_mode="HTML"
    )
    await state.set_state(AdvisorState.waiting_question)


@dp.message(AdvisorState.waiting_question)
async def advisor_question_handler(message: Message, state: FSMContext):
    """Обработка вопроса к AI-советнику"""
    await state.clear()
    
    thinking_msg = await message.answer("🤔 Анализирую...")
    
    from analyzer.ai_analyzer import ask_advisor
    answer = await ask_advisor(message.text)
    
    await thinking_msg.delete()
    await message.answer(
        f"🤖 <b>AI-советник</b>\n\n{answer}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )


@dp.callback_query(F.data.startswith("script:"))
async def negotiation_script_handler(callback: CallbackQuery):
    """Скрипты для торга"""
    listing_id = int(callback.data.split(":")[1])
    
    from database.connection import AsyncSessionLocal
    from database.models import Listing
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Listing).where(Listing.id == listing_id)
        )
        listing = result.scalar_one_or_none()
    
    if not listing:
        await callback.answer("Объявление не найдено")
        return
    
    scripts = _generate_negotiation_scripts(listing)
    
    await callback.message.answer(
        f"💬 <b>Скрипты для торга</b>\n"
        f"<i>{listing.title}</i>\n\n"
        f"{scripts}",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("analyze:"))
async def deep_analyze_handler(callback: CallbackQuery):
    """Детальный AI-разбор объявления"""
    listing_id = int(callback.data.split(":")[1])
    
    await callback.message.answer("🔍 Делаю детальный разбор...")
    
    from database.connection import AsyncSessionLocal
    from database.models import Listing
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Listing).where(Listing.id == listing_id)
        )
        listing = result.scalar_one_or_none()
    
    if not listing or not listing.ai_analysis:
        await callback.message.answer("Анализ недоступен для этого объявления")
        await callback.answer()
        return
    
    analysis = listing.ai_analysis
    text = _format_full_analysis(listing, analysis)
    
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


# ============ Alert Sender ============

async def send_alert(user_telegram_id: int, listing, analysis: dict):
    """Отправка алерта пользователю о выгодной машине"""
    
    score = analysis.get("score", 0)
    
    # Эмодзи в зависимости от скоринга
    if score >= 8:
        score_emoji = "🔥"
    elif score >= 6:
        score_emoji = "✅"
    else:
        score_emoji = "⚠️"
    
    # Формируем текст алерта
    price_diff = listing.price_diff_pct
    market_text = ""
    if price_diff and listing.market_price:
        diff_sign = "↓" if price_diff < 0 else "↑"
        market_text = (
            f"📉 Ниже рынка на {abs(price_diff):.0f}%\n"
            f"💡 Рыночная цена: {listing.market_price:,} ₽\n"
        ).replace(",", " ")
    
    urgent_text = ""
    if listing.is_urgent and listing.urgent_keywords:
        urgent_text = f"🚨 {', '.join(listing.urgent_keywords[:2]).upper()}\n"
    
    reseller_text = "👤 Частник ✅\n" if not listing.is_reseller else "🏪 Возможно перекуп ⚠️\n"
    
    resale = analysis.get("resale_potential", {})
    profit_text = ""
    if resale.get("estimated_profit", 0) > 0:
        profit_text = (
            f"\n💰 Потенциал: +{resale['estimated_profit']:,} ₽ "
            f"({resale.get('profit_probability_pct', 0)}% вероятность)\n"
            f"⏱ Продашь за ~{resale.get('estimated_days_to_sell', '?')} дней\n"
        ).replace(",", " ")
    
    text = (
        f"{score_emoji} <b>Выгодная сделка</b> — оценка {score}/10\n\n"
        f"🚗 <b>{listing.title}</b>\n\n"
        f"💰 {listing.price:,} ₽\n".replace(",", " ") +
        market_text +
        f"🛣 {listing.mileage:,} км\n".replace(",", " ") if listing.mileage else "" +
        f"📅 {listing.year} год\n" if listing.year else "" +
        f"📍 {listing.region}\n" +
        reseller_text +
        urgent_text +
        profit_text +
        f"\n🤖 <i>{analysis.get('verdict', '')}</i>"
    )
    
    keyboard = listing_actions_keyboard(listing.id, listing.url)
    
    try:
        # Отправляем фото если есть
        if listing.photos:
            await bot.send_photo(
                chat_id=user_telegram_id,
                photo=listing.photos[0],
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
    except Exception as e:
        logger.error(f"Ошибка отправки алерта пользователю {user_telegram_id}: {e}")


# ============ Helper functions ============

def _generate_negotiation_scripts(listing) -> str:
    """Генерация скриптов для торга"""
    price = listing.price
    
    # Считаем цены для торга
    offer_soft = int(price * 0.92)   # -8%
    offer_hard = int(price * 0.85)   # -15%
    offer_old = int(price * 0.88)    # -12% для долго висящих
    
    return (
        f"<b>💬 Мягкий старт:</b>\n"
        f"«Здравствуйте, смотрел объявление. Машина интересует. "
        f"Скажите, цена {price:,} — окончательная или есть возможность "
        f"договориться?»\n\n"
        f"<b>💬 С конкретным предложением:</b>\n"
        f"«Готов приехать сегодня с наличными {offer_soft:,} ₽. "
        f"Быстро оформим, без лишних вопросов. Как вам?»\n\n"
        f"<b>💬 Жёсткий торг:</b>\n"
        f"«Смотрел похожие машины, видел за {offer_hard:,}. "
        f"Могу рассмотреть вашу за эту сумму. Вам интересно?»\n\n"
        f"<b>💬 Если долго висит:</b>\n"
        f"«Вижу машина продаётся уже давно. "
        f"Готов забрать быстро за {offer_old:,} ₽, наличные.»"
    ).replace(",", " ")


def _format_full_analysis(listing, analysis: dict) -> str:
    """Форматирование полного анализа для отображения"""
    risks = analysis.get("risks", [])
    opportunities = analysis.get("opportunities", [])
    resale = analysis.get("resale_potential", {})
    photo = analysis.get("photo_analysis", {})
    
    risks_text = "\n".join(f"• {r}" for r in risks) if risks else "• Не выявлено"
    opps_text = "\n".join(f"• {o}" for o in opportunities) if opportunities else "• Не выявлено"
    
    photo_text = ""
    if photo:
        photo_text = (
            f"\n📸 <b>Анализ фото:</b>\n"
            f"Состояние: {photo.get('overall_condition', '—')}\n"
            f"{'⚠️ Признаки перекраса' if photo.get('repaint_signs') else '✅ Следов перекраса нет'}\n"
            f"{'⚠️ Стоковые фото!' if photo.get('is_stock_photo') else ''}\n"
            f"{photo.get('comment', '')}\n"
        )
    
    return (
        f"🔍 <b>Детальный AI-разбор</b>\n\n"
        f"<b>{listing.title}</b>\n"
        f"Оценка: {analysis.get('score', '—')}/10\n\n"
        f"📋 <b>Вывод:</b> {analysis.get('verdict', '—')}\n\n"
        f"⚠️ <b>Риски:</b>\n{risks_text}\n\n"
        f"✅ <b>Возможности:</b>\n{opps_text}\n\n"
        f"💰 <b>Потенциал перепродажи:</b>\n"
        f"Продать за: ~{resale.get('estimated_sell_price', 0):,} ₽\n".replace(",", " ") +
        f"Прибыль: ~{resale.get('estimated_profit', 0):,} ₽\n".replace(",", " ") +
        f"Вероятность: {resale.get('profit_probability_pct', 0)}%\n"
        f"Время продажи: ~{resale.get('estimated_days_to_sell', '?')} дней\n" +
        photo_text +
        f"\n💬 <b>Совет по торгу:</b>\n{analysis.get('negotiation_tip', '—')}"
    )


# ============ Main ============

async def main():
    from database.connection import init_db
    await init_db()
    
    logger.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

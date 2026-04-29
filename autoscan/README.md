# 🚗 AutoScan — AI-сканер выгодных автомобилей

## Быстрый старт

### 1. Клонируй репозиторий
```bash
git clone https://github.com/YOUR_USERNAME/autoscan.git
cd autoscan
```

### 2. Создай .env файл
```bash
cp .env.example .env
```

Заполни в .env:
```
TELEGRAM_BOT_TOKEN=твой_токен_от_BotFather
ANTHROPIC_API_KEY=твой_ключ_от_Anthropic
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/autoscan
REDIS_URL=redis://localhost:6379/0
```

### 3. Установи зависимости
```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Запусти PostgreSQL и Redis
```bash
# Через Docker (проще всего)
docker run -d --name postgres -e POSTGRES_PASSWORD=password -e POSTGRES_USER=user -e POSTGRES_DB=autoscan -p 5432:5432 postgres:15
docker run -d --name redis -p 6379:6379 redis:7
```

### 5. Запусти бота
```bash
python main.py
```

## Структура проекта

```
autoscan/
├── main.py              # Точка входа
├── requirements.txt     # Зависимости
├── .env.example         # Шаблон переменных окружения
├── bot/
│   └── main.py          # Telegram бот
├── parser/
│   └── avito_parser.py  # Парсер Авито
├── analyzer/
│   └── ai_analyzer.py   # AI-анализ через Claude
├── database/
│   ├── models.py        # Модели БД
│   └── connection.py    # Подключение к БД
└── scheduler/
    └── scheduler.py     # Планировщик задач
```

## Тарифные планы

| План | Цена | Фильтры | Алерты |
|------|------|---------|--------|
| Free | 0₽ | 1 | раз в час |
| Basic | 1 990₽/мес | 1 | каждые 30 мин |
| Pro | 4 990₽/мес | 3 | каждые 15 мин |
| Expert | 9 990₽/мес | 10 | каждые 5 мин |

# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**AI Tender Agent** — Telegram-бот для автоматического поиска тендеров на B2B-Center с фильтрацией и уведомлениями пользователей.

## Commands

```bash
# Запуск бота
python main.py

# Установка Playwright-браузера (нужна один раз)
python -m playwright install chromium

# Синтаксическая проверка проекта
python -m compileall -q main.py config.py tender_logic.py bot db services

# Отдельный набор автотестов пока не добавлен
```

## Architecture

```
main.py               ← Точка входа, aiogram EventLoop (ProactorEventLoop на Windows)
bot/handlers.py       ← Telegram-команды (/start, /search, /filter, /hours, etc.)
services/runner.py    ← Оркестрация одного цикла: поиск → фильтр → уведомление
services/parser.py    ← B2BParser: httpx (быстро) + Playwright (fallback для SPA/защита)
services/agent.py     ← FilterAgent: фильтрация тендеров по профилю пользователя
tender_logic.py       ← Парсинг HTML → структурированные данные (extract_b2b_data)
db/models.py          ← Pydantic-модели: TenderBase, UserProfile
db/database.py        ← Хранилище (сейчас JSON-файл profiles.json, заготовка для PostgreSQL)
config.py             ← SEARCH_QUERIES, MAX_SEARCH_PAGES, ADMIN_ID, лимиты дедлайнов
```

## Key Data Flow

1. Пользователь задаёт фильтры через команды → `handlers.py` → `profiles.json`
2. `/search` → `run_single_parsing_cycle()` в `runner.py`
3. `B2BParser` ищет по URL: `https://www.b2b-center.ru/market/?f_keyword=...&searching=1&trade=buy`
4. Для каждого тендера: httpx-запрос → если SPA/защита → Playwright-fallback
5. `tender_logic.extract_b2b_data()`: многоуровневый парсинг (Pinia/Nuxt JSON → DOM → regex)
6. `FilterAgent` проверяет: keywords → participants → deadline → online
7. Совпадающие тендеры → Telegram-уведомление (🔥 если <24ч до дедлайна)

## Critical Details

**Парсинг дедлайнов** (`tender_logic.py`): несколько fallback-стратегий — текстовые паттерны, DOM-обход, regex по raw HTML. Форматы: `DD.MM.YYYY HH:MM` (рус.) и ISO `YYYY-MM-DD`.

**SPA/Nuxt тендеры**: извлекает `window.__pinia` и `window.__NUXT__` JSON из HTML, инжектирует в DOM до парсинга.

**Правило "golden lot"**: тендеры с ≤2 участниками всегда проходят фильтр, независимо от остальных условий.

**Дубликаты**: `runner.py` хранит in-memory сет отправленных тендеров за сессию — не отправляет повторно.

**Отладка**: при провале парсинга `extract_b2b_data()` дампит HTML в `failed_tender_<id>.html`.

## Environment (.env)

```
BOT_TOKEN=              # Telegram bot token
ADMIN_ID=               # Telegram ID для ошибок парсера (опционально)
TELEGRAM_PROXY_URL=     # прокси для Telegram (опционально)
B2B_PROXY_URL=          # прокси для B2B-Center (опционально)
B2B_COOKIE=             # session cookies для B2B-Center
ANTICAPTCHA_API_KEY=    # reCAPTCHA (опционально)
RUCAPTCHA_API_KEY=      # Yandex SmartCaptcha (опционально)
```

## Storage

Сейчас хранилище — JSON-файл `data/profiles.json`. В `db/database.py` есть закомментированный код для PostgreSQL (asyncpg). Мок-данные тендеров — `data/mock_tenders.json` (только для тестов).

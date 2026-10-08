# AI Tender Agent

Telegram-бот для поиска закупок на B2B-Center, фильтрации результатов по профилю пользователя и отправки подходящих тендеров в Telegram.

## Возможности

- поиск по одному или нескольким ключевым словам;
- обход нескольких страниц выдачи B2B-Center;
- фильтрация по числу участников и времени до дедлайна;
- отдельная настройка для лотов со скрытым числом участников;
- HTTP-парсинг с Playwright fallback для SPA и защищённых страниц;
- извлечение данных из Pinia/Nuxt, DOM и raw HTML;
- сохранение пользовательских фильтров в локальный JSON-файл.

## Требования

- Python 3.10 или новее;
- Telegram bot token от [@BotFather](https://t.me/BotFather);
- Chromium для Playwright;
- авторизованная сессия B2B-Center для страниц, закрытых от гостевого доступа.

## Быстрый старт

```bash
git clone https://github.com/qkqshi/ai_tender_bot.git
cd ai_tender_bot

python -m venv .venv
```

Активация окружения в PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Активация в Linux/macOS:

```bash
source .venv/bin/activate
```

Установите зависимости и браузер:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Создайте локальный `.env` из шаблона:

```powershell
Copy-Item .env.example .env
```

В Linux/macOS:

```bash
cp .env.example .env
```

Минимально необходимо заполнить `BOT_TOKEN`. Для полноценного доступа к B2B-Center также укажите `B2B_COOKIE` и подготовьте `data/b2b_storage.json` по инструкции [data/README_STORAGE.md](data/README_STORAGE.md).

Запуск:

```bash
python main.py
```

## Переменные окружения

| Переменная | Обязательна | Назначение |
| --- | --- | --- |
| `BOT_TOKEN` | Да | Токен Telegram-бота |
| `ADMIN_ID` | Нет | Telegram ID для технических уведомлений парсера |
| `TELEGRAM_PROXY_URL` | Нет | Прокси только для Telegram API |
| `B2B_PROXY_URL` | Нет | Прокси для B2B-Center, httpx и Playwright |
| `B2B_COOKIE` | Для авторизованных страниц | Cookie-строка активной сессии B2B-Center |
| `RUCAPTCHA_API_KEY` | Нет | Ключ RuCaptcha для Yandex SmartCaptcha |
| `ANTICAPTCHA_API_KEY` | Нет | Ключ Anti-Captcha для reCAPTCHA |

## Команды бота

- `/start` — создать профиль и показать справку;
- `/filter слово1; слово2` — настроить ключевые слова;
- `/hours 48` — ограничить время до окончания тендера;
- `/participants 2` — ограничить число участников;
- `/hidden yes` — включить или исключить лоты со скрытым числом участников;
- `/pages 3` — настроить глубину поиска;
- `/settings` — показать текущие фильтры;
- `/search` — выполнить поиск по профилю;
- `/search_all` — искать по всей ленте без фильтра ключевых слов.

## Структура проекта

```text
main.py               # запуск aiogram-бота
bot/handlers.py       # Telegram-команды
services/runner.py    # поиск, фильтрация и уведомления
services/parser.py    # HTTP-парсер и Playwright fallback
services/b2b_api.py   # получение токена и запросы к B2B API
services/agent.py     # фильтры пользователя
tender_logic.py       # извлечение данных тендера из HTML
db/database.py        # локальное JSON-хранилище профилей
db/models.py          # Pydantic-модели
data/                 # мок-данные и локальное хранилище
```

## Безопасность данных

Файлы `.env`, `data/b2b_storage.json` и `data/profiles.json` содержат секреты или пользовательские данные и исключены из Git. Debug-логи, скриншоты, HTML-дампы и Python-кеш также игнорируются.

Перед публикацией можно проверить набор файлов командой:

```bash
git status --short --ignored
```

Если секрет когда-либо уже был опубликован или добавлен в коммит, одного `.gitignore` недостаточно — секрет нужно отозвать и удалить из истории Git.

## Проверка

В репозитории настроен GitHub Actions workflow, который устанавливает зависимости и компилирует Python-исходники. Локально ту же синтаксическую проверку можно запустить так:

```bash
python -m compileall -q main.py config.py tender_logic.py bot db services
```

Отдельный набор автотестов в текущей версии проекта отсутствует.

Используйте автоматизированный доступ к B2B-Center в соответствии с правилами площадки и применимыми требованиями законодательства.

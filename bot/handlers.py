from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command, CommandObject
from db.database import get_user_profile, upsert_user_profile

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, db_pool):
    await upsert_user_profile(db_pool, message.from_user.id)
    await message.answer(
        "Привет! Я AI Тендерный Агент.\n\n"
        "Настройте фильтры для поиска:\n"
        "1. Ключевые слова:\n"
        "👉 /filter станок, пресс, трубы\n\n"
        "2. Кол-во часов до конца (например, до 120ч):\n"
        "👉 /hours 48\n\n"
        "3. Макс. кол-во участников (например, до 2):\n"
        "👉 /participants 3\n\n"
        "4. Скрытые участники (видеть тендеры, где кол-во скрыто?):\n"
        "👉 /hidden yes (или /hidden no)\n\n"
        "5. Количество страниц поиска:\n"
        "👉 /pages 3\n\n"
        "📈 Чтобы увидеть текущие настройки, используйте ⚙️ /settings\n\n"
        "Для последующего ручного запуска поиска наберите 🔎 /search\n"
        "Поиск без ключевых слов (вся лента закупок, до 50 стр.): 🌐 /search_all"
    )


@router.message(Command("search"))
async def cmd_search(message: Message, db_pool):
    from services.runner import run_single_parsing_cycle
    await message.answer("🔄 Начинаю поиск тендеров по заданным параметрам. Пожалуйста, подождите...")
    result = await run_single_parsing_cycle(message.bot, db_pool, message.from_user.id)

    sent_count = result.get("sent_count", 0)
    total_found = result.get("total_found", 0)
    total_matched = result.get("total_matched", 0)
    warnings = result.get("warnings", [])
    warnings_text = "\n\n" + "\n".join(warnings) if warnings else ""
    stats_text = f"\n📊 Найдено: {total_found}, прошло фильтры: {total_matched}, отправлено: {sent_count}"

    if sent_count == 0:
        await message.answer(f"📭 Перебор завершён: новых подходящих тендеров не найдено.{stats_text}{warnings_text}")
    else:
        await message.answer(f"✅ Поиск успешно завершен!{stats_text}{warnings_text}")


@router.message(Command("search_all"))
async def cmd_search_all(message: Message, db_pool):
    """Запускает поиск по ленте без фильтра ключевых слов."""
    from services.runner import run_single_parsing_cycle
    await message.answer(
        "🔄 Запускаю поиск по всей ленте маркета (без ключевых слов).\n"
        "Применяю остальные фильтры (часы, участники, скрытые). Это может занять "
        "несколько минут — до 50 страниц по 20 лотов..."
    )
    result = await run_single_parsing_cycle(
        message.bot, db_pool, message.from_user.id, keyword_mode="all",
    )

    sent_count = result.get("sent_count", 0)
    total_found = result.get("total_found", 0)
    total_matched = result.get("total_matched", 0)
    warnings = result.get("warnings", [])
    warnings_text = "\n\n" + "\n".join(warnings) if warnings else ""
    stats_text = f"\n📊 Найдено: {total_found}, прошло фильтры: {total_matched}, отправлено: {sent_count}"

    if sent_count == 0:
        await message.answer(f"📭 Перебор всей ленты завершён: подходящих тендеров не найдено.{stats_text}{warnings_text}")
    else:
        await message.answer(f"✅ Поиск по всей ленте завершён!{stats_text}{warnings_text}")


@router.message(Command("filter"))
async def cmd_filter(message: Message, command: CommandObject, db_pool):
    if command.args is None:
        await message.answer("⚠️ Пожалуйста, укажите слова для поиска после команды, разделяя их точкой с запятой.\n\nНапример:\n/filter станок; пресс; труба")
        return

    import re

    raw_keywords = re.split(r'[;,]', command.args)
    keywords = [k.strip() for k in raw_keywords if k.strip()]

    if keywords:
        await upsert_user_profile(db_pool, message.from_user.id, keywords=keywords)
        await message.answer(f"✅ Ключевые слова для поиска обновлены:\n- " + "\n- ".join(keywords) + f"\n\nВсего слов: {len(keywords)}\nИспользуйте команду /search для запуска.")
    else:
        await message.answer("⚠️ Вы не указали ни одного корректного слова. Пожалуйста, напишите слова после команды \n(например: /filter станок, труба, насос).")


@router.message(Command("settings"))
async def cmd_settings(message: Message, db_pool):
    profile = await get_user_profile(db_pool, message.from_user.id)
    if not profile:
        await message.answer("⚠️ Профиль не найден. Используйте /start.")
        return

    keywords = ", ".join(profile.get('keywords', [])) or "не заданы"
    max_hours = profile.get('max_hours_left', 120)
    max_p = profile.get('max_participants', 2)
    hidden = "✅ Да" if profile.get('include_hidden_participants', True) else "❌ Нет"
    pages = profile.get('max_pages', 1)

    text = (
        "⚙️ Ваши текущие настройки:\n\n"
        f"🔍 Ключевые слова: {keywords}\n"
        f"⏰ Макс. часов до конца: {max_hours} ч.\n"
        f"👤 Макс. участников: {max_p}\n"
        f"❓ Скрытые участники: {hidden}\n"
        f"📄 Страниц поиска: {pages}\n\n"
        "Для изменения используйте соответствующие команды из /start"
    )
    await message.answer(text)


@router.message(Command("pages"))
async def cmd_pages(message: Message, command: CommandObject, db_pool):
    if command.args is None:
        await message.answer("⚠️ Пожалуйста, укажите количество страниц для поиска после команды.\n\nНапример:\n/pages 3")
        return

    try:
        pages = int(command.args.strip())
        if pages < 1:
            await message.answer("⚠️ Пожалуйста, укажите число страниц больше 0.")
            return

        await upsert_user_profile(db_pool, message.from_user.id, max_pages=pages)
        await message.answer(f"✅ Количество страниц для поиска успешно изменено на: {pages}")
    except ValueError:
        await message.answer("⚠️ Ошибка: значение должно быть числом (например: /pages 3).")


@router.message(Command("hours"))
async def cmd_hours(message: Message, command: CommandObject, db_pool):
    if command.args is None:
        await message.answer("⚠️ Укажите максимальное количество часов до окончания тендера.\n\nНапример:\n/hours 48")
        return
    try:
        hours = int(command.args.strip())
        await upsert_user_profile(db_pool, message.from_user.id, max_hours_left=hours)
        await message.answer(f"✅ Теперь я ищу тендеры, до окончания которых не более {hours} ч.")
    except ValueError:
        await message.answer("⚠️ Ошибка: значение должно быть числом.")


@router.message(Command("participants"))
async def cmd_participants(message: Message, command: CommandObject, db_pool):
    if command.args is None:
        await message.answer("⚠️ Укажите максимальное количество участников лота.\n\nНапример:\n/participants 2")
        return
    try:
        count = int(command.args.strip())
        await upsert_user_profile(db_pool, message.from_user.id, max_participants=count)
        await message.answer(f"✅ Теперь я ищу лоты, где не более {count} участников.")
    except ValueError:
        await message.answer("⚠️ Ошибка: значение должно быть числом.")


@router.message(Command("hidden"))
async def cmd_hidden(message: Message, command: CommandObject, db_pool):
    if command.args is None:
        await message.answer("⚠️ Укажите, включать ли тендеры со скрытым кол-вом участников (yes/no).\n\nНапример:\n/hidden yes")
        return

    val = command.args.strip().lower()
    if val in ['yes', 'y', 'true', '1', 'да']:
        include = True
        msg = "✅ Тендеры со скрытым количеством участников ВКЛЮЧЕНЫ в поиск."
    elif val in ['no', 'n', 'false', '0', 'нет']:
        include = False
        msg = "✅ Тендеры со скрытым количеством участников ИСКЛЮЧЕНЫ из поиска."
    else:
        await message.answer("⚠️ Пожалуйста, напишите 'yes' или 'no'.")
        return

    await upsert_user_profile(db_pool, message.from_user.id, include_hidden_participants=include)
    await message.answer(msg)

import logging
import os
from datetime import datetime, timezone
from aiogram import Bot

from db.database import save_tender, get_user_profile
from services.parser import B2BParser
from services.agent import FilterAgent
from config import SEARCH_QUERIES, MAX_SEARCH_PAGES, ADMIN_ID

notified_tenders = set()


async def run_single_parsing_cycle(
    bot: Bot, db_pool, user_id_to_notify: int, keyword_mode: str = "profile",
) -> dict:
    """Выполняет поиск, фильтрацию и отправку результатов."""
    global notified_tenders

    proxy_url = os.getenv("B2B_PROXY_URL") or None
    if proxy_url:
        logging.info(f"Используется прокси для B2B")
    else:
        logging.info("Прокси для B2B не используется")

    profile = await get_user_profile(db_pool, user_id_to_notify)

    if not profile:
        logging.info(f"Профиль для {user_id_to_notify} не найден, используем временный профиль.")
        profile = {
            'user_id': user_id_to_notify,
            'keywords': SEARCH_QUERIES,
            'max_pages': MAX_SEARCH_PAGES,
            'max_hours_left': 168,
            'max_participants': 5,
            'include_hidden_participants': True
        }

    if keyword_mode == "all":
        search_queries: list[str] = []
        max_pages = min(profile.get('max_pages') or MAX_SEARCH_PAGES, 50)

        profile = {**profile, 'keywords': []}
    else:
        search_queries = profile.get('keywords') or SEARCH_QUERIES
        max_pages = profile.get('max_pages') or MAX_SEARCH_PAGES

    parser = B2BParser(
        search_queries=search_queries,
        proxy_url=proxy_url,
        admin_id=ADMIN_ID,
        bot=bot,
        max_pages=max_pages,
    )
    agent = FilterAgent(db_pool)

    logging.info(f"Запуск поиска для пользователя {user_id_to_notify} (запросы: {search_queries})...")
    try:
        tenders = await parser.fetch_tenders()
    except Exception as e:
        logging.error(f"Критическая ошибка парсинга: {e}")
        tenders = []

    if not tenders:
        logging.info("Новых тендеров не найдено.")
        return {"sent_count": 0, "total_found": 0, "total_matched": 0, "warnings": []}

    notifications = []
    for tender in tenders:
        if db_pool:
            await save_tender(db_pool, tender)

        is_match = await agent.evaluate_tender_for_user(tender, profile)
        if is_match:
            notifications.append((user_id_to_notify, tender))

    logging.info(f"Найдено: {len(tenders)}, после фильтрации: {len(notifications)}")

    try:
        await bot.send_message(
            user_id_to_notify,
            f"📊 Статистика поиска:\n"
            f"• Всего найдено тендеров: {len(tenders)}\n"
            f"• Прошло фильтры: {len(notifications)}",
            parse_mode=None,
        )
    except Exception as e:
        logging.error(f"Не удалось отправить статистику {user_id_to_notify}: {e}")

    sent_count = 0
    for uid, tender in notifications:
        if uid != user_id_to_notify:
            continue

        deadline_line = ""
        if tender.deadline:
            try:
                deadline_dt = datetime.fromisoformat(tender.deadline)
                if deadline_dt.tzinfo is None:
                    now_cmp = datetime.now()
                else:
                    now_cmp = datetime.now(timezone.utc)
                delta = deadline_dt - now_cmp
                h_left = delta.total_seconds() / 3600

                if h_left < 24:
                    deadline_line = f"⏰ До окончания: {max(0, int(h_left))} ч."
                else:
                    days = int(h_left // 24)
                    rem_hours = int(h_left % 24)
                    deadline_line = f"⏰ До окончания: {days} дн. {rem_hours} ч."

                if h_left <= 24:
                    deadline_line = f"🔥 {deadline_line}"
            except Exception as e:
                logging.warning(f"Не смог распарсить deadline тендера {tender.id} ({tender.deadline!r}): {e}")

        if tender.nmcc == 0.0:
            price_line = "💰 НМЦК: ⚠️ Цена скрыта заказчиком"
        else:
            price_line = f"💰 НМЦК: {tender.nmcc:,.2f} ₽"

        profile = await get_user_profile(db_pool, uid)
        max_p = profile.get('max_participants', 2) if profile else 2

        golden_badge = ""
        if 0 <= tender.participant_count <= max_p:
            golden_badge = f"🥇 ВАШ ЛОТ! (участников <= {max_p})\n"

        if tender.participant_count == -1:
            participants_text = "⚠️ Скрыты"
        else:
            participants_text = str(tender.participant_count)

        deadline_text = f"{deadline_line}\n" if deadline_line else ""
        msg = (
            f"{golden_badge}"
            f"🔎 Найден подходящий тендер!\n\n"
            f"📌 {tender.title}\n"
            f"{price_line}\n"
            f"👤 Участников: {participants_text}\n"
            f"{deadline_text}"
            f"🌐 Онлайн: {'Да' if tender.is_online else 'Нет'}\n"
            f"📍 Площадка: B2B-Center\n"
            f"🔗 Ссылка: {tender.url}"
        )
        try:
            await bot.send_message(uid, msg, parse_mode=None)
            notified_tenders.add((uid, tender.id))
            logging.info(f"Уведомление отправлено {uid} → тендер {tender.id}")
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send message to {uid}: {e}")

    return {
        "sent_count": sent_count,
        "total_found": len(tenders),
        "total_matched": len(notifications),
        "warnings": parser.pagination_warnings,
    }

import logging
import os
import asyncio
from datetime import datetime, timezone
from aiogram import Bot

from db.database import save_tender, get_user_profile
from services.parser import B2BParser
from services.agent import FilterAgent
from config import SEARCH_QUERIES, MAX_SEARCH_PAGES, ADMIN_ID, DEADLINE_MIN_HOURS, DEADLINE_MAX_HOURS

# Множество для предотвращения повторной отправки одних и тех же лотов (в рамках жизни бота)
notified_tenders = set()

async def run_single_parsing_cycle(
    bot: Bot, db_pool, user_id_to_notify: int, keyword_mode: str = "profile",
) -> dict:
    """
    Выполняет один цикл поиска, фильтрации и рассылки.

    Args:
        keyword_mode:
            "profile" (default) — поиск по keywords из профиля юзера;
            "all" — поиск без слова, перебор всей ленты закупок (cap 50 страниц).
            В режиме "all" фильтр keywords в agent.py отключается автоматически
            (т.к. keywords=[] для разового вызова).

    Возвращает {"sent_count": N, "warnings": [...]}.
    """
    global notified_tenders

    proxy_url = os.getenv("B2B_PROXY_URL") or None
    if proxy_url:
        logging.info(f"🌐 Используется прокси для B2B")
    else:
        logging.info("🔓 Прокси для B2B не используется")

    # Получаем профиль или используем дефолтные настройки
    profile = await get_user_profile(db_pool, user_id_to_notify)

    if not profile:
        logging.info(f"Профиль для {user_id_to_notify} не найден, используем временный профиль.")
        profile = {
            'user_id': user_id_to_notify,
            'keywords': SEARCH_QUERIES,
            'max_pages': MAX_SEARCH_PAGES,
            'max_hours_left': 168, # 7 дней по умолчанию
            'max_participants': 5, # 5 участников по умолчанию
            'include_hidden_participants': True
        }

    if keyword_mode == "all":
        # Разовый прогон по всей ленте: keywords пустые (parser идёт без f_keyword,
        # agent пропускает фильтр по словам — см. services/agent.py:29).
        search_queries: list[str] = []
        max_pages = min(profile.get('max_pages') or MAX_SEARCH_PAGES, 50)
        # Подменяем профиль для агента, оставив остальные фильтры на месте
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

    logging.info(f"🔄 Запуск поиска для пользователя {user_id_to_notify} (запросы: {search_queries})...")
    try:
        tenders = await parser.fetch_tenders()
    except Exception as e:
        logging.error(f"💥 Критическая ошибка парсинга: {e}")
        tenders = []

    if not tenders:
        logging.info("📭 Новых тендеров не найдено.")
        return {"sent_count": 0, "total_found": 0, "total_matched": 0, "warnings": []}

    # Сохраняем и фильтруем тендеры специально для этого пользователя
    notifications = []
    for tender in tenders:
        if db_pool:
            await save_tender(db_pool, tender)
            
        is_match = await agent.evaluate_tender_for_user(tender, profile)
        if is_match:
            notifications.append((user_id_to_notify, tender))
            
    logging.info(f"📋 Найдено: {len(tenders)}, после фильтрации: {len(notifications)}")

    # Промежуточный отчёт юзеру — чтобы видел сразу, сколько лотов всего попалось
    # и сколько из них прошло его фильтры (до начала рассылки самих карточек).
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

        # Для РУЧНОГО поиска (run_single_parsing_cycle) мы НЕ пропускаем уже уведомленные тендеры,
        # так как пользователь хочет видеть текущий срез результатов.
        # if (uid, tender.id) in notified_tenders:
        #     continue

        # Формируем текст дедлайна для сообщения.
        # SPA-тендеры (через API) дают ISO с таймзоной → aware,
        # старые view.html — без → naive. Сравнивать с now нужно с одной стороны,
        # иначе TypeError проглатывается except'ом и в TG нет дедлайна.
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
                logging.warning(f"⚠️ Не смог распарсить deadline тендера {tender.id} ({tender.deadline!r}): {e}")

        if tender.nmcc == 0.0:
            price_line = "💰 НМЦК: ⚠️ Цена скрыта заказчиком"
        else:
            price_line = f"💰 НМЦК: {tender.nmcc:,.2f} ₽"

        # Получаем профиль для отображения бейджа (опционально)
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
            logging.info(f"✅ Уведомление отправлено {uid} → тендер {tender.id}")
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send message to {uid}: {e}")

    return {
        "sent_count": sent_count,
        "total_found": len(tenders),
        "total_matched": len(notifications),
        "warnings": parser.pagination_warnings,
    }

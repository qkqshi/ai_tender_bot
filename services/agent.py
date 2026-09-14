from typing import List
from db.models import TenderBase


class FilterAgent:
    """Фильтрует тендеры по профилю пользователя."""
    def __init__(self, db_pool=None):
        self.db_pool = db_pool

    async def evaluate_tender_for_user(self, tender: TenderBase, profile: dict) -> bool:
        """Проверяет соответствие тендера настройкам профиля."""
        from datetime import datetime, timezone
        import logging
        logger = logging.getLogger(__name__)

        tender_id = tender.id
        tender_title = tender.title[:50] + "..." if len(tender.title) > 50 else tender.title

        keywords = profile.get('keywords', [])
        if keywords:
            text_to_search = (tender.title + " " + (str(tender.raw_json) if tender.raw_json else "")).lower()
            if not any(k.lower() in text_to_search for k in keywords):
                logger.info(f"Тендер {tender_id} ({tender_title}) отсеян: не найдены ключевые слова {keywords}")
                return False

        logger.info(f"Тендер {tender_id} ({tender_title}) прошел фильтр ключевых слов")

        p_count = tender.participant_count
        max_p = profile.get('max_participants', 2)
        include_hidden = profile.get('include_hidden_participants', True)

        if p_count == -1:
            if not include_hidden:
                logger.info(f"Тендер {tender_id} ({tender_title}) отсеян: участники скрыты (include_hidden=False)")
                return False
            else:
                logger.info(f"Тендер {tender_id} ({tender_title}) прошел фильтр участников: скрыты, но разрешены")
        else:
            if p_count > max_p:
                logger.info(f"Тендер {tender_id} ({tender_title}) отсеян: участников {p_count} > {max_p}")
                return False
            else:
                logger.info(f"Тендер {tender_id} ({tender_title}) прошел фильтр участников: {p_count} <= {max_p}")

        if tender.deadline:
            try:
                deadline_dt = datetime.fromisoformat(tender.deadline)

                if deadline_dt.tzinfo is None:
                    now = datetime.now()
                else:
                    now = datetime.now(timezone.utc)

                hours_left = (deadline_dt - now).total_seconds() / 3600
                max_hours = profile.get('max_hours_left', 120)

                if hours_left < 0:
                    logger.info(f"Тендер {tender_id} ({tender_title}) отсеян: дедлайн уже прошел ({hours_left:.1f} ч)")
                    return False

                if hours_left > max_hours:
                    logger.info(f"Тендер {tender_id} ({tender_title}) отсеян: до дедлайна {hours_left:.1f} ч > {max_hours} ч")
                    return False

                logger.info(f"Тендер {tender_id} ({tender_title}) прошел фильтр дедлайна: {hours_left:.1f} ч <= {max_hours} ч")
            except ValueError as e:
                logger.warning(f"Тендер {tender_id} ({tender_title}): ошибка парсинга дедлайна {tender.deadline}: {e}")
                pass
        else:
            logger.info(f"Тендер {tender_id} ({tender_title}): дедлайн не указан, пропускаем проверку")

        if profile.get('only_online') and not tender.is_online:
            logger.info(f"Тендер {tender_id} ({tender_title}) отсеян: требуется онлайн, но is_online=False")
            return False

        logger.info(f"Тендер {tender_id} ({tender_title}) ПРОШЕЛ ВСЕ ФИЛЬТРЫ!")
        return True

    async def run_filtering_pipeline(self, tenders: List[TenderBase]):
        """Возвращает тендеры и пользователей, прошедших фильтрацию."""
        from db.database import get_all_user_profiles
        if not self.db_pool:
            return []

        users = await get_all_user_profiles(self.db_pool)

        notifications = []

        for tender in tenders:
            for user in users:
                user_dict = dict(user)
                is_match = await self.evaluate_tender_for_user(tender, user_dict)
                if is_match:
                    notifications.append((user_dict['user_id'], tender))

        return notifications

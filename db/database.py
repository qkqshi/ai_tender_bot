import os
from dotenv import load_dotenv

load_dotenv()

import json

PROFILES_FILE = "data/profiles.json"
MOCK_PROFILES = {}
MOCK_TENDERS = {}

if not os.path.exists("data"):
    os.makedirs("data")


def load_profiles():
    global MOCK_PROFILES
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

                MOCK_PROFILES = {int(k): v for k, v in data.items()}
                print(f"Загружено {len(MOCK_PROFILES)} профилей из файла.")
        except Exception as e:
            print(f"Ошибка загрузки профилей: {e}")


def save_profiles():
    try:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(MOCK_PROFILES, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения профилей: {e}")

load_profiles()


class DummyPool:
    pass


async def get_pool():
    return DummyPool()


async def init_db(pool):
    print("Инициализация (PostgreSQL отключен, сохранение в profiles.json)")


async def get_all_user_profiles(pool):
    return list(MOCK_PROFILES.values())


async def get_user_profile(pool, user_id):
    return MOCK_PROFILES.get(user_id)


async def upsert_user_profile(pool, user_id, target_participants=None, only_online=None, keywords=None, max_pages=None, max_hours_left=None, max_participants=None, include_hidden_participants=None):
    profile = MOCK_PROFILES.get(user_id)
    if not profile:
        MOCK_PROFILES[user_id] = {
            'user_id': user_id,
            'target_participants': target_participants or [],
            'keywords': keywords or [],
            'regions': [],
            'only_online': only_online or False,
            'max_pages': max_pages or 1,
            'max_hours_left': max_hours_left or 168,
            'max_participants': max_participants or 5,
            'include_hidden_participants': include_hidden_participants if include_hidden_participants is not None else True
        }
    else:
        if target_participants is not None:
            profile['target_participants'] = target_participants
        if only_online is not None:
            profile['only_online'] = only_online
        if keywords is not None:
            profile['keywords'] = keywords
        if max_pages is not None:
            profile['max_pages'] = max_pages
        if max_hours_left is not None:
            profile['max_hours_left'] = max_hours_left
        if max_participants is not None:
            profile['max_participants'] = max_participants
        if include_hidden_participants is not None:
            profile['include_hidden_participants'] = include_hidden_participants

    save_profiles()


async def save_tender(pool, tender):
    if tender.id not in MOCK_TENDERS:
        MOCK_TENDERS[tender.id] = tender
        print(f"Тендер {tender.id} ({tender.title}) сохранен в памяти!")

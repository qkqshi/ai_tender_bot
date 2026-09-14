import os

SEARCH_QUERIES = [
    "пресс",
]

MAX_SEARCH_PAGES = 10
ADMIN_ID = int(os.getenv("ADMIN_ID") or "0") or None

DEADLINE_MIN_HOURS = 1
DEADLINE_MAX_HOURS = 120

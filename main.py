import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from db.database import get_pool, init_db
from bot.handlers import router

load_dotenv()
logging.basicConfig(level=logging.INFO)

async def main():
    proxy_url = os.getenv("TELEGRAM_PROXY_URL") or None
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    bot = Bot(token=bot_token, session=session)
    dp = Dispatcher()

    # Init DB
    db_pool = await get_pool()
    await init_db(db_pool)

    # Inject db_pool into dispatcher to be accessible in handlers
    dp.workflow_data.update({'db_pool': db_pool})

    # Register routes
    dp.include_router(router)

    # Start bot
    logging.info("🤖 AI Tender Agent is starting poll loop...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        # Используем ProactorEventLoop для совместимости с Playwright
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())

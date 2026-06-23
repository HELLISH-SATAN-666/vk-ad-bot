from __future__ import annotations

import asyncio
import logging
import os
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database.base import close_pool
from database.schema import ensure_schema
from utils.config import BASE_DIR, active_token_and_group
from utils.services import add_day_posters_events, check_for_all_events, delete_expired_purchases
from vkbot.api import VKApi, VKLongPoll
from vkbot.app import VKBotApp
from vkbot.handlers import register_handlers


logger = logging.getLogger(__name__)


async def main() -> None:
    os.chdir(BASE_DIR)
    load_dotenv(BASE_DIR / ".env")

    token, group_id = active_token_and_group()
    api = VKApi(token=token, group_id=group_id)
    await api.open()

    try:
        if not group_id:
            own_group = await api.get_own_group()
            group_id = own_group.id
            api.group_id = group_id

        await api.enable_long_poll(group_id)
        await ensure_schema()

        app = VKBotApp(api)
        register_handlers(app)

        scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        scheduler.add_job(delete_expired_purchases, "cron", hour=0, minute=1)
        scheduler.add_job(add_day_posters_events, "cron", hour=0, minute=10)
        scheduler.add_job(check_for_all_events, "interval", minutes=1, args=[api])
        scheduler.start()

        logger.info("VK bot started for group_id=%s", group_id)
        long_poll = VKLongPoll(api, group_id)
        async for update in long_poll.listen():
            await app.handle_update(update)
    finally:
        await api.close()
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    asyncio.run(main())

from __future__ import annotations

import asyncio
import logging
import os
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database.base import close_pool, init_pool
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
    pool = None
    lock_conn = None

    try:
        if not group_id:
            own_group = await api.get_own_group()
            group_id = own_group.id
            api.group_id = group_id

        await api.enable_long_poll(group_id)
        await ensure_schema()
        pool = await init_pool()
        lock_conn = await pool.acquire()
        lock_acquired = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1::bigint);", int(group_id))
        if not lock_acquired:
            logger.error("Another VK bot instance is already running for group_id=%s", group_id)
            return

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
        if lock_conn is not None:
            try:
                await lock_conn.execute("SELECT pg_advisory_unlock($1::bigint);", int(group_id or 0))
                if pool is not None:
                    await pool.release(lock_conn)
            except Exception:
                logger.exception("Failed to release bot instance lock")
        await api.close()
        await close_pool()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    asyncio.run(main())

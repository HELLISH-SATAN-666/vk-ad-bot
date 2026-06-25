from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database.base import close_pool, init_pool
from database import PartnerTypes, VkGroups
from database.schema import ensure_schema
from utils.config import BASE_DIR, active_token_and_group
from utils.services import add_day_posters_events, check_for_all_events, delete_expired_purchases
from vkbot.api import VKApi, VKLongPoll
from vkbot.app import VKBotApp
from vkbot.handlers import register_handlers


logger = logging.getLogger(__name__)
PARTNER_LONG_POLL_REFRESH_SECONDS = 60


async def run_long_poll(app: VKBotApp, api: VKApi, group_id: int, guard_only: bool = False) -> None:
    try:
        await api.enable_long_poll(group_id)
        logger.info("VK long poll started for group_id=%s guard_only=%s", group_id, guard_only)
        long_poll = VKLongPoll(api, group_id)
        async for update in long_poll.listen():
            await app.handle_update(update, api=api, guard_only=guard_only)
    finally:
        await api.close()


async def manage_partner_long_polls(app: VKBotApp, main_group_id: int, api_version: str) -> None:
    tasks: dict[int, tuple[str, VKApi, asyncio.Task]] = {}
    statuses = [int(PartnerTypes.SUB_GROUPS), int(PartnerTypes.PROMOTION_AND_SUB)]
    try:
        while True:
            async with VkGroups() as vk_groups:
                rows = await vk_groups.get_partner_long_poll_groups(statuses)
            wanted = {
                int(row["group_id"]): row["token"]
                for row in rows
                if int(row["group_id"]) != abs(int(main_group_id))
            }

            for group_id, (token, group_api, task) in list(tasks.items()):
                if group_id not in wanted or wanted[group_id] != token or task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                    tasks.pop(group_id, None)

            for group_id, token in wanted.items():
                if group_id in tasks:
                    continue
                group_api = VKApi(token, group_id=group_id, api_version=api_version)
                task = asyncio.create_task(run_long_poll(app, group_api, group_id, guard_only=True))
                tasks[group_id] = (token, group_api, task)

            await asyncio.sleep(PARTNER_LONG_POLL_REFRESH_SECONDS)
    finally:
        for _, _, task in tasks.values():
            task.cancel()
        for _, _, task in tasks.values():
            with suppress(asyncio.CancelledError, Exception):
                await task


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
        partner_polls = asyncio.create_task(manage_partner_long_polls(app, group_id, api.api_version))
        try:
            long_poll = VKLongPoll(api, group_id)
            async for update in long_poll.listen():
                await app.handle_update(update)
        finally:
            partner_polls.cancel()
            with suppress(asyncio.CancelledError):
                await partner_polls
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

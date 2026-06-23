from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import (
    AdGroups,
    AdGroupsStatus,
    ManualPayments,
    Newsletters,
    PartnerGroups,
    Partners,
    Payments,
    PaymentTypes,
    Posters,
    PosterStatus,
    Queue,
    Users,
    UsersSubs,
)
from database.base import close_pool
from database.schema import ensure_schema
from utils.config import BASE_DIR, get_ad_categories
from utils.services import get_msk_now
from vkbot.api import VKGroupInfo
from vkbot.app import VKBotApp
from vkbot.handlers import register_handlers


class FakeVKApi:
    def __init__(self):
        self.group_id = 239792902
        self.api_version = "5.199"
        self.sent: list[dict[str, Any]] = []
        self.groups: dict[int, VKGroupInfo] = {}

    async def send_message(self, peer_id: int, message: str, keyboard=None, attachment=None, disable_mentions=True):
        self.sent.append(
            {
                "peer_id": peer_id,
                "message": message,
                "keyboard": keyboard,
                "attachment": attachment,
            }
        )
        return len(self.sent)

    async def resolve_group(self, raw: str) -> VKGroupInfo:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            raise ValueError(raw)
        group_id = int(digits)
        info = VKGroupInfo(id=group_id, screen_name=f"club{group_id}", name=f"Group {group_id}")
        self.groups[group_id] = info
        return info

    async def group_title(self, group_id: int) -> str:
        return self.groups.get(abs(group_id), VKGroupInfo(abs(group_id), "", f"Group {abs(group_id)}")).name

    async def is_group_member(self, group_id: int, user_id: int) -> bool:
        return True

    async def wall_post(self, group_id: int, message: str, attachments=None, from_group=True):
        self.sent.append({"wall_group_id": group_id, "message": message, "attachment": attachments})
        return len(self.sent)

    async def delete_message(self, message_id: int, delete_for_all: bool = True):
        self.sent.append({"deleted_message_id": message_id, "delete_for_all": delete_for_all})


async def recreate_test_db() -> None:
    admin_conn = await asyncpg.connect(
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database="postgres",
    )
    try:
        await admin_conn.execute("DROP DATABASE IF EXISTS vk_ad_bot_test WITH (FORCE);")
        await admin_conn.execute("CREATE DATABASE vk_ad_bot_test;")
    finally:
        await admin_conn.close()
    os.environ["DB_NAME"] = "vk_ad_bot_test"
    await ensure_schema()


def payload(cmd: str, **kwargs) -> str:
    return json.dumps({"cmd": cmd, **kwargs}, ensure_ascii=False)


async def send(app: VKBotApp, user_id: int, text: str = "", cmd: str | None = None, peer_id: int | None = None, ref: str | None = None, **payload_kwargs) -> None:
    message = {"from_id": user_id, "peer_id": peer_id or user_id, "text": text, "attachments": [], "id": 1000 + len(app.api.sent)}
    if ref:
        message["ref"] = ref
    if cmd:
        message["payload"] = payload(cmd, **payload_kwargs)
    await app.handle_update({"type": "message_new", "object": {"message": message}})


async def first_manual_payment_id() -> int:
    async with ManualPayments() as payments:
        rows = await payments.get_all()
    assert rows, "manual payment was not created"
    return rows[0]["id"]


async def main() -> None:
    load_dotenv(BASE_DIR / ".env", override=True)
    os.environ["DB_NAME"] = "vk_ad_bot_test"
    os.environ["ADMINS"] = "1113916884"
    await recreate_test_db()

    api = FakeVKApi()
    app = VKBotApp(api)  # type: ignore[arg-type]
    register_handlers(app)

    admin_id = 1113916884
    partner_id = 222000001
    advertiser_id = 222000002

    await send(app, partner_id, "start")
    await send(app, partner_id, cmd="add_bot_group")
    await send(app, partner_id, "club3001 token=partner-token")
    await send(app, partner_id, "0")
    await send(app, partner_id, cmd="select_category", category_id="0")
    await send(app, partner_id, cmd="confirm_select_ad_categories")

    async with PartnerGroups() as partner_groups:
        groups = await partner_groups.get_all(creator_id=partner_id, status=None)
        assert len(groups) == 1
        partner_group_db_id = groups[0]["id"]
    async with Partners() as partners:
        assert await partners.in_db(partner_id)

    await send(app, admin_id, "start")
    await send(app, admin_id, cmd="manage_partner_groups_admin")
    await send(app, admin_id, cmd="partner_group_act.promotion_and_sub", group_id=partner_group_db_id)

    first_category = next(iter(get_ad_categories().values()))
    await send(app, advertiser_id, "start")
    await send(app, advertiser_id, cmd="buy_ad.poster")
    await send(app, advertiser_id, f"{first_category}\nТестовое объявление")
    await send(app, advertiser_id, "0")
    await send(app, advertiser_id, "1")
    await send(app, advertiser_id, "Оплата тестового объявления")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)

    async with Posters() as posters:
        all_posters = await posters.get_all()
        assert len(all_posters) == 1
        assert all_posters[0]["creator_id"] == advertiser_id
        poster_id = all_posters[0]["id"]
        assert await posters.get_by_status(PosterStatus.MODERATED)

    await send(app, admin_id, cmd="poster_change_button", poster_id=poster_id)
    await send(app, admin_id, "Новая кнопка")
    async with Posters() as posters:
        poster = await posters.get_by_id(poster_id)
        assert poster["referral_button_name"] == "Новая кнопка"

    await send(app, admin_id, cmd="poster_schedule_send", poster_id=poster_id)
    await send(app, admin_id, cmd="poster_schedule_group", num="1")
    schedule_time = (get_msk_now() + timedelta(minutes=5)).strftime("%H:%M")
    await send(app, admin_id, schedule_time)
    async with Queue() as queue:
        events = await queue.get_group_events(3001)
        assert any(event["poster_id"] == poster_id for event in events)

    await send(app, advertiser_id, cmd="buy_ad.group")
    await send(app, advertiser_id, "club4001")
    await send(app, advertiser_id, cmd="select_group", num="1")
    await send(app, advertiser_id, cmd="confirm_select_groups")
    await send(app, advertiser_id, "1")
    await send(app, advertiser_id, "Оплата тестовой группы")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)

    async with AdGroups() as ad_groups:
        groups = await ad_groups.get_all()
        assert len(groups) == 1
        assert groups[0]["group_id"] == 4001
        assert await ad_groups.get_by_status(AdGroupsStatus.ACTIVE)

    async with Payments() as payments:
        ad_group_pays = await payments.get_all(pay_type=PaymentTypes.AD_GROUP)
        assert len(ad_group_pays) == 1
        assert ad_group_pays[0]["type"] == PaymentTypes.AD_GROUP

    subscriber_id = 222000003
    await send(app, subscriber_id, "start", ref="3001")
    await send(app, subscriber_id, cmd="check_subs", main_group_id=3001)
    async with UsersSubs() as subs:
        assert await subs.in_db(subscriber_id, 3001)

    chat_peer_id = 2_000_003_001
    async with PartnerGroups() as partner_groups:
        await partner_groups.add(partner_id, chat_peer_id, [0], ["0"], partner_type=1)
        await partner_groups.add_need_groups(chat_peer_id, 4001)
    await send(app, subscriber_id, "chat message", peer_id=chat_peer_id)
    async with UsersSubs() as subs:
        assert await subs.in_db(subscriber_id, chat_peer_id)

    await send(app, advertiser_id, cmd="buy_ad.newsletter")
    await send(app, advertiser_id, "Тестовая рассылка")
    await send(app, advertiser_id, cmd="ad_newsletter_target.partners")
    await send(app, advertiser_id, "1")
    await send(app, advertiser_id, "Оплата тестовой рассылки")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)

    async with Newsletters() as newsletters:
        nls = await newsletters.get_all(is_sub=True, is_moderating=True)
        assert len(nls) == 1
        nl_id = nls[0]["id"]

    await send(app, admin_id, cmd="newsletter_act.apply", nl_id=nl_id)
    async with Newsletters() as newsletters:
        nls = await newsletters.get_all(is_sub=True, is_moderating=False)
        assert len(nls) == 1

    await send(app, partner_id, cmd="create_money_request")
    async with Users() as users:
        assert await users.in_db(partner_id)
        assert await users.in_db(advertiser_id)

    await close_pool()
    print("scenario tests ok")


if __name__ == "__main__":
    asyncio.run(main())

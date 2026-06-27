from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import time, timedelta
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
    NewslettersTarget,
    PartnerGroups,
    PartnerTypes,
    Partners,
    Payments,
    PaymentTypes,
    Posters,
    PosterStatus,
    Queue,
    UserRequests,
    Users,
    UsersSubs,
    VkGroups,
)
from database.base import close_pool
from database.partner_groups import normalize_sub_rates
from database.schema import ensure_schema
from utils.config import BASE_DIR, get_ad_categories
from utils.services import check_for_nl_events, check_for_poster_events, get_msk_now
from vkbot.api import VKGroupInfo
from vkbot.api import normalize_group_ref
from vkbot.app import VKBotApp
import vkbot.handlers as vk_handlers
from vkbot.handlers import register_handlers


class FakeVKApi:
    def __init__(self):
        self.group_id = 239792902
        self.api_version = "5.199"
        self.sent: list[dict[str, Any]] = []
        self.groups: dict[int, VKGroupInfo] = {}
        self.members: dict[tuple[int, int], bool] = {}
        self.chat_titles: dict[int, str] = {}
        self.chat_members: dict[tuple[int, int], bool] = {}

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
        if int(group_id) > 2_000_000_000:
            return await self.chat_title(int(group_id))
        return self.groups.get(abs(group_id), VKGroupInfo(abs(group_id), "", f"Group {abs(group_id)}")).name

    async def chat_title(self, peer_id: int) -> str:
        return self.chat_titles.get(int(peer_id), f"Chat {int(peer_id) - 2_000_000_000}")

    async def is_chat_member(self, peer_id: int, user_id: int) -> bool:
        return self.chat_members.get((int(peer_id), int(user_id)), True)

    async def chat_invite_link(self, peer_id: int) -> str:
        return f"https://vk.com/join/chat{int(peer_id) - 2_000_000_000}"

    async def target_link(self, group_id: int) -> str:
        if int(group_id) > 2_000_000_000:
            return await self.chat_invite_link(int(group_id))
        return f"https://vk.com/club{abs(int(group_id))}"

    async def is_group_member(self, group_id: int, user_id: int) -> bool:
        if int(group_id) > 2_000_000_000:
            return await self.is_chat_member(int(group_id), user_id)
        return self.members.get((abs(int(group_id)), int(user_id)), True)

    async def is_group_manager(self, user_id: int, group_id: int | None = None) -> bool:
        return user_id == 1113916884

    async def delete_message(self, message_id: int, delete_for_all: bool = True, peer_id: int | None = None, conversation_message_id: int | None = None):
        self.sent.append(
            {
                "deleted_message_id": message_id,
                "deleted_peer_id": peer_id,
                "deleted_conversation_message_id": conversation_message_id,
                "delete_for_all": delete_for_all,
            }
        )

    async def wall_post(self, owner_id: int, message: str, attachments=None, from_group=True):
        self.sent.append(
            {
                "wall_group_id": abs(int(owner_id)),
                "message": message,
                "attachment": attachments,
                "from_group": from_group,
            }
        )
        return len(self.sent)

    async def delete_wall_post(self, owner_id: int, post_id: int):
        self.sent.append({"deleted_wall_group_id": abs(int(owner_id)), "deleted_wall_post_id": post_id})

    async def get_user_name(self, user_id: int) -> str:
        return f"User {user_id}"


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


_MESSAGE_ID = 1000


def next_message_id() -> int:
    global _MESSAGE_ID
    _MESSAGE_ID += 1
    return _MESSAGE_ID


async def send(app: VKBotApp, user_id: int, text: str = "", cmd: str | None = None, peer_id: int | None = None, ref: str | None = None, **payload_kwargs) -> None:
    sent_before = len(app.api.sent)
    message_id = next_message_id()
    message = {
        "from_id": user_id,
        "peer_id": peer_id or user_id,
        "text": text,
        "attachments": [],
        "id": message_id,
        "conversation_message_id": message_id,
    }
    if ref:
        message["ref"] = ref
    if cmd:
        message["payload"] = payload(cmd, **payload_kwargs)
    await app.handle_update({"type": "message_new", "object": {"message": message}})
    new_messages = app.api.sent[sent_before:]
    assert not any(
        "Произошла ошибка. Я записал" in item.get("message", "")
        for item in new_messages
    ), f"handler failed for text={text!r}, cmd={cmd!r}: {new_messages!r}"


async def chat_action(app: VKBotApp, user_id: int, peer_id: int, action: dict[str, Any], api: FakeVKApi | None = None) -> None:
    active_api = api or app.api
    sent_before = len(active_api.sent)
    message_id = next_message_id()
    message = {
        "from_id": user_id,
        "peer_id": peer_id,
        "text": "",
        "attachments": [],
        "id": message_id,
        "conversation_message_id": message_id,
        "action": action,
    }
    await app.handle_update({"type": "message_new", "object": {"message": message}}, api=api)  # type: ignore[arg-type]
    new_messages = active_api.sent[sent_before:]
    assert not any(
        "Произошла ошибка. Я записал" in item.get("message", "")
        for item in new_messages
    ), f"handler failed for chat action={action!r}: {new_messages!r}"


async def first_manual_payment_id() -> int:
    async with ManualPayments() as payments:
        rows = await payments.get_all()
    assert rows, "manual payment was not created"
    return rows[0]["id"]


async def manual_payment_count() -> int:
    async with ManualPayments() as payments:
        return len(await payments.get_all())


async def main() -> None:
    assert normalize_group_ref("https://vk.com/club123456?from=search") == "123456"
    assert normalize_group_ref("m.vk.com/public987#wall") == "987"
    assert normalize_group_ref("@screen_name") == "screen_name"

    load_dotenv(BASE_DIR / ".env", override=True)
    os.environ["DB_NAME"] = "vk_ad_bot_test"
    os.environ["ADMINS"] = "1113916884"
    await recreate_test_db()

    api = FakeVKApi()
    app = VKBotApp(api)  # type: ignore[arg-type]
    vk_handlers.SUBSCRIPTION_PROMPT_TTL_SECONDS = 0
    register_handlers(app)

    admin_id = 1113916884
    partner_id = 222000001
    advertiser_id = 222000002
    reject_advertiser_id = 222000004
    wall_group_id = 3001
    extra_wall_group_id = 3002
    partner_chat_id = 2_000_003_001
    extra_partner_chat_id = 2_000_003_002
    ad_chat_id = 2_000_004_001
    access_chat_id = 2_000_005_001
    need_chat_id = 2_000_006_001
    paid_setup_chat_id = 2_000_007_001
    api.chat_titles.update(
        {
            partner_chat_id: "Partner chat",
            extra_partner_chat_id: "Extra partner chat",
            ad_chat_id: "Advertiser chat",
            access_chat_id: "Paid access chat",
            need_chat_id: "Need chat",
            paid_setup_chat_id: "Client paid setup chat",
        }
    )

    await send(app, partner_id, "start")
    await send(app, partner_id, cmd="add_bot_group")
    await send(app, partner_id, "club3001 token=partner-token")
    assert "Сообщество найдено" in app.api.sent[-1]["message"]
    await send(app, partner_id, "0")
    await send(app, partner_id, cmd="select_category", category_id="0")
    await send(app, partner_id, cmd="confirm_select_ad_categories")

    await send(app, partner_id, cmd="add_bot_group")
    await send(app, partner_id, "club3002")
    assert "Нужно отправить ссылку/ID сообщества и ключ доступа" in app.api.sent[-1]["message"]
    await send(app, partner_id, "назад", cmd="main_menu")

    async with PartnerGroups() as partner_groups:
        groups = await partner_groups.get_all(creator_id=partner_id, status=None)
        assert len(groups) == 1
        partner_group_db_id = groups[0]["id"]
        assert groups[0]["group_id"] == wall_group_id
    async with VkGroups() as vk_groups:
        saved_group = await vk_groups.get(wall_group_id)
        assert saved_group is not None
        assert saved_group["target_type"] == "community"
        assert saved_group["token"] == "partner-token"
        assert saved_group["can_wall_post"]
    async with Partners() as partners:
        assert await partners.in_db(partner_id)

    async with PartnerGroups() as partner_groups:
        await partner_groups.add(partner_id, partner_chat_id, [0], [0], PartnerTypes.PROMOTION_AND_SUB)
        sub_partner_group = await partner_groups.get_by_group_id(partner_chat_id)
        sub_partner_group_db_id = sub_partner_group["id"]
    async with VkGroups() as vk_groups:
        await vk_groups.upsert(partner_chat_id, title="Partner chat", screen_name="", target_type="chat")

    await send(app, admin_id, "start")
    await send(app, admin_id, "/id")
    sent_before = len(app.api.sent)
    await send(app, admin_id, "/id", peer_id=partner_chat_id)
    assert any(
        item.get("peer_id") == admin_id and f"VK peer_id этой беседы: {partner_chat_id}" in item.get("message", "")
        for item in app.api.sent[sent_before:]
    )
    assert any(item.get("deleted_peer_id") == partner_chat_id for item in app.api.sent[sent_before:])
    assert not any(
        item.get("peer_id") == partner_chat_id and item.get("message")
        for item in app.api.sent[sent_before:]
    )
    await send(app, admin_id, cmd="menu_adminpanel")
    await send(app, admin_id, cmd="manage_all_ads")
    await send(app, admin_id, cmd="manage_partner_groups_admin")
    await send(app, admin_id, cmd="partner_group_need_groups", group_id=sub_partner_group_db_id)
    sent_before = len(app.api.sent)
    await chat_action(app, admin_id, need_chat_id, {"type": "chat_invite_user", "member_id": -api.group_id})
    assert any(
        item.get("peer_id") == admin_id and "Беседа добавлена в условия подписки" in item.get("message", "")
        for item in app.api.sent[sent_before:]
    )
    assert not any(
        item.get("peer_id") == need_chat_id and item.get("message")
        for item in app.api.sent[sent_before:]
    )
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(sub_partner_group_db_id)
        assert need_chat_id in (group["need_groups"] or [])
    await send(app, admin_id, cmd="partner_group_need_groups", group_id=sub_partner_group_db_id)
    await send(app, admin_id, "0")
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(sub_partner_group_db_id)
        assert not (group["need_groups"] or [])
    await send(app, admin_id, cmd="partner_group_need_groups", group_id=sub_partner_group_db_id)
    await send(app, admin_id, str(need_chat_id))
    await send(app, admin_id, cmd="partner_group_schedule", group_id=sub_partner_group_db_id)

    first_category = next(iter(get_ad_categories().values()))
    await send(app, advertiser_id, "start", ref=str(partner_chat_id))
    await send(app, advertiser_id, cmd="menu_advertiser")
    await send(app, advertiser_id, cmd="paid_plains")
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

    await send(app, admin_id, cmd="poster_select_groups", poster_id=poster_id)
    wall_num = str((app.state.get_data(admin_id)["select_group_values"]).index(wall_group_id) + 1)
    await send(app, admin_id, cmd="select_group", num=wall_num)
    await send(app, admin_id, cmd="confirm_poster_groups")
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(partner_group_db_id)
        assert poster_id in (group["show_ad_ids"] or [])

    await send(app, admin_id, cmd="poster_select_groups", poster_id=poster_id)
    wall_num = str((app.state.get_data(admin_id)["select_group_values"]).index(wall_group_id) + 1)
    await send(app, admin_id, cmd="select_group", num=wall_num)
    await send(app, admin_id, cmd="confirm_poster_groups")
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(partner_group_db_id)
        assert poster_id not in (group["show_ad_ids"] or [])

    await send(app, admin_id, cmd="poster_act.activate", poster_id=poster_id)
    async with Posters() as posters:
        assert (await posters.get_by_id(poster_id))["status"] == PosterStatus.ACTIVE
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(partner_group_db_id)
        assert poster_id in (group["show_ad_ids"] or [])
    await send(app, admin_id, cmd="poster_act.freeze", poster_id=poster_id)
    async with Posters() as posters:
        assert (await posters.get_by_id(poster_id))["status"] == PosterStatus.FROZEN

    await send(app, admin_id, cmd="poster_schedule_send", poster_id=poster_id)
    await send(app, admin_id, cmd="poster_schedule_group", num="1")
    schedule_time = (get_msk_now() + timedelta(minutes=5)).strftime("%H:%M")
    await send(app, admin_id, schedule_time)
    async with Queue() as queue:
        events = await queue.get_group_events(wall_group_id)
        assert any(event["poster_id"] == poster_id for event in events)

    await send(app, reject_advertiser_id, "start")
    await send(app, reject_advertiser_id, cmd="buy_ad.poster")
    await send(app, reject_advertiser_id, f"{first_category}\nОтклоняемое объявление")
    await send(app, reject_advertiser_id, "0")
    await send(app, reject_advertiser_id, "1")
    await send(app, reject_advertiser_id, "Отклонить оплату")
    reject_pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.decline", pay_id=reject_pay_id)
    assert await manual_payment_count() == 0

    await send(app, advertiser_id, cmd="buy_ad.group")
    assert "какой доступ по подписке" in app.api.sent[-1]["message"]
    await send(app, advertiser_id, cmd="buy_group_access_mode.none")
    await send(app, advertiser_id, "club4001")
    assert "peer_id VK-беседы" in app.api.sent[-1]["message"]
    sent_before = len(app.api.sent)
    await chat_action(app, advertiser_id, ad_chat_id, {"type": "chat_invite_user", "member_id": -api.group_id})
    assert any(
        item.get("peer_id") == advertiser_id and "Беседа может быть добавлена" in item.get("message", "")
        for item in app.api.sent[sent_before:]
    )
    assert not any(
        item.get("peer_id") == ad_chat_id and item.get("message")
        for item in app.api.sent[sent_before:]
    )
    await send(app, advertiser_id, cmd="select_group", num="1")
    await send(app, advertiser_id, cmd="confirm_select_groups")
    await send(app, advertiser_id, "1")
    await send(app, advertiser_id, "Оплата тестовой группы")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)

    async with AdGroups() as ad_groups:
        groups = await ad_groups.get_all()
        assert len(groups) == 1
        assert groups[0]["group_id"] == ad_chat_id
        assert await ad_groups.get_by_status(AdGroupsStatus.ACTIVE)
        ad_group_db_id = groups[0]["id"]

    async with Payments() as payments:
        ad_group_pays = await payments.get_all(pay_type=PaymentTypes.AD_GROUP)
        assert len(ad_group_pays) == 1
        assert ad_group_pays[0]["type"] == PaymentTypes.AD_GROUP
    async with VkGroups() as vk_groups:
        protected_vk_group = await vk_groups.get(ad_chat_id)
        assert protected_vk_group is not None
        assert protected_vk_group["target_type"] == "chat"

    paid_setup_user_id = 222000014
    await send(app, paid_setup_user_id, "start")
    await send(app, paid_setup_user_id, cmd="buy_ad.group")
    await send(app, paid_setup_user_id, cmd="buy_group_access_mode.time")
    await send(app, paid_setup_user_id, str(paid_setup_chat_id))
    assert any("Режим доступа: по времени" in item.get("message", "") for item in app.api.sent[-3:])
    async with PartnerGroups() as partner_groups:
        paid_setup_group = await partner_groups.get_by_group_id(paid_setup_chat_id)
        paid_setup_group_db_id = paid_setup_group["id"]
        assert paid_setup_group["creator_id"] == paid_setup_user_id
        assert int(paid_setup_group["partner_type"]) == int(PartnerTypes.SUB_GROUPS)
        assert str(paid_setup_group["sub_rate_type"]).strip() == "time"
        assert normalize_sub_rates(paid_setup_group["sub_rates"])["time"]
    await send(app, admin_id, cmd="partner_group_edit_rates", group_id=paid_setup_group_db_id, rate_type="time")
    await send(app, admin_id, "14 99\n30 150")
    await send(app, admin_id, cmd="partner_group_rate_type.time", group_id=paid_setup_group_db_id)
    async with PartnerGroups() as partner_groups:
        paid_setup_group = await partner_groups.get_by_db_id(paid_setup_group_db_id)
        assert normalize_sub_rates(paid_setup_group["sub_rates"])["time"][0] == {"days": 14, "price_rub": 99}
    paid_setup_reader_id = 222000015
    await send(app, paid_setup_reader_id, "start", ref=str(paid_setup_chat_id))
    assert "Доступные тарифы" in app.api.sent[-1]["message"]
    assert "14 дн." in app.api.sent[-1]["message"]

    ad_chat_guard_api = FakeVKApi()
    ad_chat_guard_api.chat_titles = api.chat_titles
    ad_chat_guard_user_id = 222000011
    ad_chat_guard_api.chat_members[(partner_chat_id, ad_chat_guard_user_id)] = False
    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": ad_chat_guard_user_id,
                    "peer_id": ad_chat_id,
                    "text": "Сообщение в купленной беседе без подписки на выбранную площадку",
                    "attachments": [],
                    "id": 9401,
                    "conversation_message_id": 9401,
                }
            },
        },
        api=ad_chat_guard_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert not ad_chat_guard_api.sent

    await send(app, admin_id, cmd="manage_ad_groups")
    await send(app, admin_id, cmd="open_ad_group", item_id=ad_group_db_id)
    await send(app, admin_id, cmd="ad_group_act.freeze", ad_group_id=ad_group_db_id)
    async with AdGroups() as ad_groups:
        assert (await ad_groups.get_by_db_id(ad_group_db_id))["status"] == AdGroupsStatus.FROZEN
    await send(app, admin_id, cmd="ad_group_act.activate", ad_group_id=ad_group_db_id)
    await send(app, admin_id, cmd="ad_group_select_groups", ad_group_id=ad_group_db_id)
    await send(app, admin_id, cmd="confirm_ad_group_groups")
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(sub_partner_group_db_id)
        assert ad_chat_id in (group["need_groups"] or [])

    blocked_chat_api = FakeVKApi()
    blocked_chat_api.chat_titles = api.chat_titles
    blocked_chat_user_id = 222000006
    blocked_chat_api.chat_members[(ad_chat_id, blocked_chat_user_id)] = False
    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": blocked_chat_user_id,
                    "peer_id": partner_chat_id,
                    "text": "Сообщение без вступления в рекламируемую беседу",
                    "attachments": [],
                    "id": 9001,
                    "conversation_message_id": 77,
                }
            },
        },
        api=blocked_chat_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert any(item.get("deleted_peer_id") == partner_chat_id and item.get("deleted_conversation_message_id") == 77 for item in blocked_chat_api.sent)
    assert any(item.get("peer_id") == partner_chat_id and "Для того чтобы написать сообщения" in item.get("message", "") for item in blocked_chat_api.sent)
    async with UsersSubs() as subs:
        assert not await subs.in_db(blocked_chat_user_id, partner_chat_id)

    sent_before = len(blocked_chat_api.sent)
    await app.handle_update(
        {
            "type": "wall_post_new",
            "group_id": 3001,
            "object": {"id": 78, "owner_id": -3001, "from_id": blocked_chat_user_id, "text": "Стена игнорируется"},
        },
        api=blocked_chat_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert len(blocked_chat_api.sent) == sent_before

    allowed_chat_user_id = 222000007
    await send(app, allowed_chat_user_id, "Сообщение с доступом", peer_id=partner_chat_id)
    async with UsersSubs() as subs:
        assert await subs.in_db(allowed_chat_user_id, partner_chat_id)

    subscriber_id = 222000003
    await send(app, subscriber_id, "start", ref=str(partner_chat_id))
    await send(app, subscriber_id, cmd="check_subs", main_group_id=partner_chat_id)
    async with UsersSubs() as subs:
        assert await subs.in_db(subscriber_id, partner_chat_id)

    resubscribe_api = FakeVKApi()
    resubscribe_api.chat_titles = api.chat_titles
    resubscribe_api.chat_members[(ad_chat_id, subscriber_id)] = False
    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": subscriber_id,
                    "peer_id": partner_chat_id,
                    "text": "Сообщение после отписки от обязательной беседы",
                    "attachments": [],
                    "id": 9051,
                    "conversation_message_id": 9051,
                }
            },
        },
        api=resubscribe_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert any(item.get("deleted_peer_id") == partner_chat_id and item.get("deleted_conversation_message_id") == 9051 for item in resubscribe_api.sent)
    assert any(item.get("peer_id") == partner_chat_id and "Необходимо подписаться на группы и не отписываться" in item.get("message", "") for item in resubscribe_api.sent)

    await send(app, admin_id, cmd="ad_group_select_groups", ad_group_id=ad_group_db_id)
    sub_partner_num = str((app.state.get_data(admin_id)["select_group_values"]).index(partner_chat_id) + 1)
    await send(app, admin_id, cmd="select_group", num=sub_partner_num)
    await send(app, admin_id, cmd="confirm_ad_group_groups")
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(sub_partner_group_db_id)
        assert ad_chat_id not in (group["need_groups"] or [])

    await send(app, admin_id, cmd="partner_group_need_groups", group_id=sub_partner_group_db_id)
    await send(app, admin_id, "0")

    await send(app, partner_id, cmd="add_bot_group")
    await send(app, partner_id, str(access_chat_id))
    assert any("Беседа доступа добавлена" in item.get("message", "") for item in app.api.sent[-3:])
    async with PartnerGroups() as partner_groups:
        access_group = await partner_groups.get_by_group_id(access_chat_id)
        access_group_db_id = access_group["id"]
        assert access_group["creator_id"] == partner_id
        assert int(access_group["partner_type"]) == int(PartnerTypes.SUB_GROUPS)
        assert str(access_group["sub_rate_type"]).strip() == "none"
    async with VkGroups() as vk_groups:
        access_vk_group = await vk_groups.get(access_chat_id)
        assert access_vk_group is not None
        assert access_vk_group["target_type"] == "chat"

    await send(app, partner_id, cmd="partner_group_need_groups", group_id=access_group_db_id)
    await send(app, partner_id, str(need_chat_id))
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(access_group_db_id)
        assert need_chat_id in (group["need_groups"] or [])

    free_access_user_id = 222000012
    free_access_api = FakeVKApi()
    free_access_api.chat_titles = api.chat_titles
    free_access_api.chat_members[(need_chat_id, free_access_user_id)] = False
    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": free_access_user_id,
                    "peer_id": access_chat_id,
                    "text": "Сообщение без обязательной подписки",
                    "attachments": [],
                    "id": 9300,
                    "conversation_message_id": 9300,
                }
            },
        },
        api=free_access_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert any(item.get("deleted_peer_id") == access_chat_id and item.get("deleted_conversation_message_id") == 9300 for item in free_access_api.sent)
    assert any(item.get("peer_id") == access_chat_id and "Для того чтобы написать сообщения" in item.get("message", "") for item in free_access_api.sent)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert any(item.get("deleted_message_id") for item in free_access_api.sent)
    free_access_api.chat_members[(need_chat_id, free_access_user_id)] = True
    sent_before = len(free_access_api.sent)
    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": free_access_user_id,
                    "peer_id": access_chat_id,
                    "text": "Проверить подписку",
                    "payload": payload("check_subs", main_group_id=access_chat_id),
                    "attachments": [],
                    "id": 9304,
                    "conversation_message_id": 9304,
                }
            },
        },
        api=free_access_api,  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    new_free_access_events = free_access_api.sent[sent_before:]
    assert any(item.get("deleted_peer_id") == access_chat_id and item.get("deleted_conversation_message_id") == 9304 for item in new_free_access_events)
    assert any(item.get("peer_id") == access_chat_id and "Подписка подтверждена" in item.get("message", "") for item in new_free_access_events)
    assert any(item.get("deleted_message_id") for item in new_free_access_events)
    async with UsersSubs() as subs:
        assert await subs.in_db(free_access_user_id, access_chat_id)

    await send(app, partner_id, cmd="partner_group_need_groups", group_id=access_group_db_id)
    await send(app, partner_id, "0")
    await send(app, partner_id, cmd="partner_group_edit_rates", group_id=access_group_db_id, rate_type="msg")
    await send(app, partner_id, "2 30\n5 60")
    await send(app, partner_id, cmd="partner_group_rate_type.msg", group_id=access_group_db_id)
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(access_group_db_id)
        assert str(group["sub_rate_type"]).strip() == "msg"
        assert normalize_sub_rates(group["sub_rates"])["msg"][0] == {"msg": 2, "price_rub": 30}

    access_paid_msg_user_id = 222000013
    await send(app, access_paid_msg_user_id, "start", ref=str(access_chat_id))
    assert "Доступные тарифы" in app.api.sent[-1]["message"]
    await send(app, access_paid_msg_user_id, cmd="buy_group_access", main_group_id=access_chat_id, rate_index=0)
    await send(app, access_paid_msg_user_id, "Оплата доступа в беседу по сообщениям")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)
    async with UsersSubs() as subs:
        sub = await subs.get_sub(access_paid_msg_user_id, access_chat_id)
        assert sub is not None
        assert str(sub["type"]).strip() == "msg"
        assert sub["msg_left"] == 2

    access_paid_msg_api = FakeVKApi()
    access_paid_msg_api.chat_titles = api.chat_titles
    for msg_id in (9301, 9302):
        await app.handle_update(
            {
                "type": "message_new",
                "object": {
                    "message": {
                        "from_id": access_paid_msg_user_id,
                        "peer_id": access_chat_id,
                        "text": "Оплаченное сообщение в беседе доступа",
                        "attachments": [],
                        "id": msg_id,
                        "conversation_message_id": msg_id,
                    }
                },
            },
            api=access_paid_msg_api,  # type: ignore[arg-type]
            guard_only=True,
        )
    async with UsersSubs() as subs:
        assert not await subs.in_db(access_paid_msg_user_id, access_chat_id)

    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": access_paid_msg_user_id,
                    "peer_id": access_chat_id,
                    "text": "Сообщение сверх лимита в беседе доступа",
                    "attachments": [],
                    "id": 9303,
                    "conversation_message_id": 9303,
                }
            },
        },
        api=access_paid_msg_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert any(item.get("deleted_peer_id") == access_chat_id and item.get("deleted_conversation_message_id") == 9303 for item in access_paid_msg_api.sent)
    assert any(item.get("peer_id") == access_chat_id and "Рад видеть тебя в группе" in item.get("message", "") for item in access_paid_msg_api.sent)

    await send(app, admin_id, cmd="partner_group_rates", group_id=sub_partner_group_db_id)
    await send(app, admin_id, cmd="partner_group_edit_rates", group_id=sub_partner_group_db_id, rate_type="msg")
    await send(app, admin_id, "2 30\n5 60")
    await send(app, admin_id, cmd="partner_group_rate_type.msg", group_id=sub_partner_group_db_id)
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(sub_partner_group_db_id)
        assert str(group["sub_rate_type"]).strip() == "msg"
        assert normalize_sub_rates(group["sub_rates"])["msg"][0] == {"msg": 2, "price_rub": 30}

    paid_msg_user_id = 222000008
    await send(app, paid_msg_user_id, "start", ref=str(partner_chat_id))
    assert "Доступные тарифы" in app.api.sent[-1]["message"]
    await send(app, paid_msg_user_id, cmd="buy_group_access", main_group_id=partner_chat_id, rate_index=0)
    await send(app, paid_msg_user_id, "Оплата доступа по сообщениям")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)
    async with UsersSubs() as subs:
        sub = await subs.get_sub(paid_msg_user_id, partner_chat_id)
        assert sub is not None
        assert str(sub["type"]).strip() == "msg"
        assert sub["msg_left"] == 2
    async with Payments() as payments:
        sub_access_pays = await payments.get_all(pay_type=PaymentTypes.SUB_ACCESS)
        assert len(sub_access_pays) >= 2

    paid_msg_api = FakeVKApi()
    paid_msg_api.chat_titles = api.chat_titles
    for msg_id in (9101, 9102):
        await app.handle_update(
            {
                "type": "message_new",
                "object": {
                    "message": {
                        "from_id": paid_msg_user_id,
                        "peer_id": partner_chat_id,
                        "text": "Оплаченное сообщение",
                        "attachments": [],
                        "id": msg_id,
                        "conversation_message_id": msg_id,
                    }
                },
            },
            api=paid_msg_api,  # type: ignore[arg-type]
            guard_only=True,
        )
    async with UsersSubs() as subs:
        assert not await subs.in_db(paid_msg_user_id, partner_chat_id)

    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": paid_msg_user_id,
                    "peer_id": partner_chat_id,
                    "text": "Сообщение сверх лимита",
                    "attachments": [],
                    "id": 9103,
                    "conversation_message_id": 9103,
                }
            },
        },
        api=paid_msg_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert any(item.get("deleted_message_id") == 9103 for item in paid_msg_api.sent)
    assert any(item.get("peer_id") == partner_chat_id and "Рад видеть тебя в группе" in item.get("message", "") for item in paid_msg_api.sent)

    await send(app, admin_id, cmd="partner_group_edit_rates", group_id=sub_partner_group_db_id, rate_type="time")
    await send(app, admin_id, "7 70")
    await send(app, admin_id, cmd="partner_group_rate_type.time", group_id=sub_partner_group_db_id)
    paid_time_user_id = 222000009
    await send(app, paid_time_user_id, "start", ref=str(partner_chat_id))
    await send(app, paid_time_user_id, cmd="buy_group_access", main_group_id=partner_chat_id, rate_index=0)
    await send(app, paid_time_user_id, "Оплата доступа по времени")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)
    async with UsersSubs() as subs:
        sub = await subs.get_sub(paid_time_user_id, partner_chat_id)
        assert sub is not None
        assert str(sub["type"]).strip() == "time"
        assert sub["expires_at"] > get_msk_now()

    paid_time_api = FakeVKApi()
    paid_time_api.chat_titles = api.chat_titles
    sent_before = len(paid_time_api.sent)
    await app.handle_update(
        {
            "type": "message_new",
            "object": {
                "message": {
                    "from_id": paid_time_user_id,
                    "peer_id": partner_chat_id,
                    "text": "Сообщение с оплаченным доступом по времени",
                    "attachments": [],
                    "id": 9201,
                    "conversation_message_id": 9201,
                }
            },
        },
        api=paid_time_api,  # type: ignore[arg-type]
        guard_only=True,
    )
    assert not any(item.get("deleted_message_id") == 9201 for item in paid_time_api.sent[sent_before:])

    await send(app, admin_id, cmd="partner_group_rate_type.none", group_id=sub_partner_group_db_id)

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

    await send(app, admin_id, cmd="open_newsletter", item_id=nl_id)
    await send(app, admin_id, cmd="newsletter_change_time", nl_id=nl_id)
    await send(app, admin_id, "16:30")
    await send(app, admin_id, cmd="newsletter_change_expires", nl_id=nl_id)
    new_expires = (get_msk_now() + timedelta(days=3)).date()
    await send(app, admin_id, new_expires.strftime("%d.%m.%Y"))
    async with Newsletters() as newsletters:
        nl = await newsletters.get_by_id(nl_id)
        assert nl["send_time"] == time(16, 30)
        assert nl["expires_at"] == new_expires

    await send(app, admin_id, cmd="newsletter_act.apply", nl_id=nl_id)
    async with Newsletters() as newsletters:
        nls = await newsletters.get_all(is_sub=True, is_moderating=False)
        assert len(nls) == 1
        assert nls[0]["send_time"] == time(16, 30)
        await newsletters.update_send_time(nl_id, get_msk_now().time().replace(second=0, microsecond=0))
    sent_before = len(api.sent)
    await check_for_nl_events(api)  # type: ignore[arg-type]
    assert any(
        item.get("peer_id") == partner_id and item.get("message") == "Тестовая рассылка"
        for item in api.sent[sent_before:]
    )
    assert not any(
        item.get("peer_id") == partner_chat_id and item.get("message") == "Тестовая рассылка"
        for item in api.sent[sent_before:]
    )

    await send(app, advertiser_id, cmd="buy_ad.newsletter")
    await send(app, advertiser_id, "Рассылка на удаление")
    await send(app, advertiser_id, cmd="ad_newsletter_target.subs")
    await send(app, advertiser_id, "1")
    await send(app, advertiser_id, "Оплата удаляемой рассылки")
    pay_id = await first_manual_payment_id()
    await send(app, admin_id, cmd="manual_pay.apply", pay_id=pay_id)
    async with Newsletters() as newsletters:
        nls = await newsletters.get_all(is_sub=True, is_moderating=True)
        delete_nl_id = nls[0]["id"]
    await send(app, admin_id, cmd="newsletter_moderation")
    await send(app, admin_id, cmd="open_newsletter", item_id=delete_nl_id)
    await send(app, admin_id, cmd="newsletter_act.delete", nl_id=delete_nl_id)
    async with Newsletters() as newsletters:
        nls = await newsletters.get_all(is_sub=True, is_moderating=True)
        assert not nls

    await send(app, admin_id, cmd="admin_newsletter")
    await send(app, admin_id, cmd="answer_newsletter_to.partners")
    await send(app, admin_id, "Админская рассылка партнерам")
    assert any(
        item.get("peer_id") == partner_id and item.get("message") == "Админская рассылка партнерам"
        for item in api.sent
    )
    async with Newsletters() as newsletters:
        direct_nls = await newsletters.get_all()
        assert any(nl["target"] == NewslettersTarget.PARTNERS for nl in direct_nls)

    await send(app, admin_id, cmd="admin_newsletter")
    await send(app, admin_id, cmd="answer_newsletter_to.sub")
    await send(app, admin_id, cmd="select_newsletter_user", item_id=subscriber_id)
    await send(app, admin_id, "Личная рассылка подписчику")
    assert any(
        item.get("peer_id") == subscriber_id and item.get("message") == "Личная рассылка подписчику"
        for item in api.sent
    )
    async with Newsletters() as newsletters:
        direct_nls = await newsletters.get_all()
        assert any(nl["target"] == NewslettersTarget.SUB for nl in direct_nls)

    await send(app, admin_id, cmd="statistics")
    assert "newsletter_statistics" in (api.sent[-1].get("keyboard") or "")
    await send(app, admin_id, cmd="statistic.partners")
    assert "Рефералы" in api.sent[-1]["message"]
    await send(app, admin_id, cmd="change_partner.next")
    await send(app, admin_id, cmd="statistic.subscribes")
    assert "Все подписчики" in api.sent[-1]["message"]
    await send(app, admin_id, cmd="newsletter_statistics")
    await send(app, admin_id, cmd="newsletter_statistics_admin")
    assert "Все рассылки от администраторов" in api.sent[-1]["message"]
    await send(app, admin_id, cmd="change_admin_nls_page.next")
    await send(app, admin_id, cmd="newsletter_statistics_advert")
    assert "Рассылка от рекламодателя" in api.sent[-1]["message"]
    await send(app, admin_id, cmd="change_advert_nl.next")
    await send(app, admin_id, cmd="subs_stat_menu")
    await send(app, admin_id, cmd="settings_plains")
    await send(app, admin_id, cmd="admin_var_settings")
    await send(app, advertiser_id, cmd="my_ads")

    async with Partners() as partners:
        partner = await partners.get_user(partner_id)
        assert partner["balance"] >= 2
    await send(app, partner_id, cmd="create_money_request")
    await send(app, partner_id, "2")
    await send(app, partner_id, "Карта 1234")
    async with UserRequests() as requests:
        reqs = await requests.get_requests(partner_id)
        assert len(reqs) == 1
        request_id = reqs[0]["id"]
    await send(app, admin_id, cmd="manage_requests")
    await send(app, admin_id, cmd="request_act.approve", request_id=request_id)
    async with UserRequests() as requests:
        reqs = await requests.get_requests(partner_id)
        assert reqs[0]["status"] == 2

    await send(app, partner_id, cmd="add_bot_group")
    await send(app, partner_id, "club3002 token=extra-token")
    assert "Сообщество найдено" in app.api.sent[-1]["message"]
    await send(app, partner_id, "0")
    await send(app, partner_id, cmd="select_category", category_id="0")
    await send(app, partner_id, cmd="confirm_select_ad_categories")
    async with PartnerGroups() as partner_groups:
        groups = await partner_groups.get_all(creator_id=partner_id, status=None)
        extra_group = next(group for group in groups if group["group_id"] == extra_wall_group_id)
        assert extra_group["partner_type"] == PartnerTypes.PROMOTION
    await send(app, partner_id, cmd="manage_partner_groups")
    await send(app, partner_id, cmd="open_partner_group", item_id=extra_group["id"])
    await send(app, partner_id, cmd="partner_group_act.freeze", group_id=extra_group["id"])
    async with PartnerGroups() as partner_groups:
        assert (await partner_groups.get_by_db_id(extra_group["id"]))["partner_type"] == PartnerTypes.FROZEN
    await send(app, partner_id, cmd="partner_group_act.delete", group_id=extra_group["id"])
    async with PartnerGroups() as partner_groups:
        assert await partner_groups.get_by_db_id(extra_group["id"]) is None

    async with Queue() as queue:
        await queue.add(get_msk_now() - timedelta(minutes=1), wall_group_id, poster_id)
    await check_for_poster_events(api)  # type: ignore[arg-type]
    async with Queue() as queue:
        assert not await queue.get_events(get_msk_now())
    assert any(item.get("wall_group_id") == wall_group_id and item.get("message") for item in api.sent)

    await send(app, admin_id, cmd="poster_act.delete", poster_id=poster_id)
    async with Posters() as posters:
        assert await posters.get_by_id(poster_id) is None
    await send(app, admin_id, cmd="ad_group_act.delete", ad_group_id=ad_group_db_id)
    async with AdGroups() as ad_groups:
        assert await ad_groups.get_by_db_id(ad_group_db_id) is None

    async with Users() as users:
        assert await users.in_db(partner_id)
        assert await users.in_db(advertiser_id)

    await close_pool()
    print("scenario tests ok")


if __name__ == "__main__":
    asyncio.run(main())

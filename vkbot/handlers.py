from __future__ import annotations

import logging
import asyncio
import re
from datetime import date, time, timedelta
from os import environ, getenv
from typing import Any

from dotenv import set_key

from database import (
    AdGroups,
    AdGroupsStatus,
    EventType,
    ManualPayments,
    Newsletters,
    PartnerGroups,
    PartnerTypes,
    Partners,
    Posters,
    PosterStatus,
    Queue,
    RequestStatus,
    RequestType,
    UserRequests,
    Users,
    UsersSubs,
    UserStatus,
    VkGroups,
)
from database.partner_groups import normalize_sub_rates
from utils import keyboards as kb
from utils import texts
from utils.config import BASE_DIR, get_ad_categories, get_admins, get_regions, get_settings_var_names
from utils.services import (
    activate_payment_state,
    add_counter,
    edit_rate,
    get_msk_now,
    get_suitable_groups,
    moderate_newsletter,
    reject_payment,
    send_log,
    send_newsletter,
)
from vkbot.app import Ctx, VKBotApp
from vkbot.api import VKApi, is_vk_chat_peer_id


logger = logging.getLogger(__name__)
SUBSCRIPTION_PROMPT_TTL_SECONDS = 90


def _is_config_admin(user_id: int) -> bool:
    return user_id in get_admins()


async def _is_admin(ctx: Ctx) -> bool:
    if _is_config_admin(ctx.user_id):
        return True
    try:
        return await ctx.api.is_group_manager(ctx.user_id)
    except Exception:
        logger.exception("Failed to check VK community admin rights for user=%s", ctx.user_id)
        return False


async def _admin_required(ctx: Ctx) -> bool:
    if await _is_admin(ctx):
        return True
    await ctx.answer("Нет доступа.")
    return False


def _selected(data: dict[str, Any], key: str = "selected") -> set[str]:
    value = data.get(key) or set()
    return set(value)


def _validate_regions(raw: str) -> list[str] | None:
    values = raw.split()
    if not values or not all(value.isdigit() for value in values):
        return None
    regions = get_regions()
    for value in values:
        if value not in regions:
            return None
    return ["0"] if values[0] == "0" else values


async def _group_titles(api, group_ids: list[int]) -> dict[int, str]:
    result = {}
    for group_id in group_ids:
        result[group_id] = await api.group_title(group_id)
    return result


def _payment_state_from_data(data: dict[str, Any]) -> dict[str, Any]:
    state = {
        "ad_type": data["ad_type"],
        "from_user": data["from_user"],
        "sub_period": data["sub_period"],
        "current_pay_info": data["current_pay_info"],
    }
    match data["ad_type"]:
        case "poster":
            state["poster_info"] = data["poster_info"]
            state["region_codes"] = list(map(int, data["region_codes"]))
        case "group":
            state["ad_group_id"] = data["ad_group_id"]
            state["selected_group_ids"] = list(map(int, data.get("selected_group_ids") or []))
            state["ad_group_title"] = data.get("ad_group_title")
            state["ad_group_screen_name"] = data.get("ad_group_screen_name")
            state["ad_group_token"] = data.get("ad_group_token")
        case "newsletter":
            state["newsletter_text"] = data["newsletter_text"]
            state["newsletter_attachment"] = data.get("newsletter_attachment")
            state["newsletter_target"] = data["newsletter_target"]
        case "sub_access":
            state["access_group_id"] = int(data["access_group_id"])
            state["access_rate_type"] = data["access_rate_type"]
            state["access_rate"] = data["access_rate"]
    return state


def _split_group_refs(text: str) -> list[str]:
    refs = []
    for part in text.replace("\n", " ").replace(",", " ").replace(";", " ").split():
        ref = part.strip()
        if ref:
            refs.append(ref)
    return refs


def _extract_group_and_token(text: str) -> tuple[str, str | None]:
    text = text.strip()
    marker = "token="
    marker_pos = text.casefold().find(marker)
    if marker_pos != -1:
        group_ref = text[:marker_pos].strip()
        token_parts = text[marker_pos + len(marker) :].strip().split()
        return group_ref, token_parts[0] if token_parts else None

    parts = text.split()
    if len(parts) >= 2 and (parts[-1].startswith("vk1.") or len(parts[-1]) >= 40):
        return " ".join(parts[:-1]).strip(), parts[-1]

    return text, None


def _parse_vk_chat_peer_id(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None

    if "token=" in text.casefold() or "vk1." in text:
        return None

    match = re.search(r"(?<!\d)(2\d{9,})(?!\d)", text)
    if match and is_vk_chat_peer_id(match.group(1)):
        return int(match.group(1))

    match = re.search(r"(?:^|\s)c(?:hat)?[_-]?(\d{1,9})(?:\s|$)", text.casefold())
    if match:
        return 2_000_000_000 + int(match.group(1))

    return None


def _is_bot_invite_action(ctx: Ctx) -> bool:
    action = ctx.message.get("action") or {}
    action_type = str(action.get("type") or "")
    if action_type not in {"chat_invite_user", "chat_invite_user_by_link"}:
        return False

    raw_member_id = action.get("member_id")
    if raw_member_id is None:
        members = action.get("member_ids") or []
        raw_member_id = members[0] if members else None
    if raw_member_id is None:
        return ctx.state.get_state(ctx.user_id) in {"partner_group_id", "buy_group", "partner_group_need_groups"}

    try:
        member_id = int(raw_member_id)
    except (TypeError, ValueError):
        return False
    return bool(ctx.api.group_id) and abs(member_id) == abs(int(ctx.api.group_id))


async def _save_chat_meta(ctx: Ctx, chat_peer_id: int) -> str:
    group_name = await ctx.api.chat_title(chat_peer_id)
    async with VkGroups() as vk_groups:
        await vk_groups.upsert(chat_peer_id, title=group_name, screen_name="", target_type="chat")
    return group_name


async def _delete_message_later(api: VKApi, peer_id: int, message_id: int, delay: int = SUBSCRIPTION_PROMPT_TTL_SECONDS) -> None:
    if not message_id:
        return
    await asyncio.sleep(delay)
    try:
        await api.delete_message(int(message_id), delete_for_all=True)
    except Exception:
        logger.exception("Failed to delete temporary VK bot message peer_id=%s message_id=%s", peer_id, message_id)


def _schedule_temporary_delete(api: VKApi, peer_id: int, message_id: int) -> None:
    if message_id:
        asyncio.create_task(_delete_message_later(api, peer_id, message_id, delay=SUBSCRIPTION_PROMPT_TTL_SECONDS))


async def _answer_private_safely(ctx: Ctx, text: str, keyboard: str | None = None, attachment: str | None = None) -> int:
    try:
        return await ctx.answer_private(text, keyboard=keyboard, attachment=attachment)
    except Exception:
        logger.exception("Failed to send private VK message to user=%s from chat peer_id=%s", ctx.user_id, ctx.peer_id)
        return 0


async def _answer_flow(ctx: Ctx, text: str, *, private: bool = False, keyboard: str | None = None, attachment: str | None = None) -> int:
    if private:
        return await _answer_private_safely(ctx, text, keyboard=keyboard, attachment=attachment)
    return await ctx.answer(text, keyboard=keyboard, attachment=attachment)


async def _show_buy_group_partner_selector(ctx: Ctx, chat_peer_id: int, group_name: str, *, private: bool = False) -> None:
    ctx.update_data(
        ad_group_id=chat_peer_id,
        ad_group_title=group_name,
        ad_group_screen_name="",
        ad_group_token=None,
    )
    async with PartnerGroups() as partner_groups:
        partner_group_ids = await partner_groups.get_all_ids()
    if not partner_group_ids:
        await _answer_flow(
            ctx,
            "Пока нет партнерских площадок для подписочной рекламы. Обратитесь к администратору.",
            private=private,
            keyboard=kb.advertiser_menu(),
        )
        ctx.clear_state()
        return

    titles = await _group_titles(ctx.api, partner_group_ids)
    text = [texts.ADD_GROUP_SUCCESSFUL_TEXT, ""]
    for index, group_id in enumerate(partner_group_ids, 1):
        text.append(f"{index}) {titles[group_id]}")
    ctx.update_data(
        select_mode="buy_group",
        select_group_values=partner_group_ids,
        selected=set(),
    )
    await _answer_flow(
        ctx,
        "\n".join(text),
        private=private,
        keyboard=kb.number_select_kb(len(partner_group_ids), set(), "confirm_select_groups", "buy_ad"),
    )
    ctx.set_state(None)


async def _create_access_chat_group(ctx: Ctx, chat_peer_id: int, group_name: str) -> Any | None:
    async with VkGroups() as vk_groups:
        await vk_groups.upsert(chat_peer_id, title=group_name, screen_name="", target_type="chat")
    async with PartnerGroups() as partner_groups:
        await partner_groups.add(ctx.user_id, chat_peer_id, [0], [0], PartnerTypes.SUB_GROUPS)
        group = await partner_groups.get_by_group_id(chat_peer_id)
    async with Partners() as partners:
        if not await partners.in_db(ctx.user_id):
            await partners.add(ctx.user_id)
            await add_counter(EventType.ADDED_NEW_PARTNER)
    async with Users() as users:
        if not await users.in_db(ctx.user_id):
            await users.add(ctx.user_id)
        await users.update_status(ctx.user_id, UserStatus.PARTNER)
    return group


async def _maybe_complete_chat_binding(ctx: Ctx) -> bool:
    if not ctx.is_chat or not _is_bot_invite_action(ctx):
        return False

    state = ctx.state.get_state(ctx.user_id)
    chat_peer_id = ctx.peer_id
    if not state:
        await _answer_private_safely(
            ctx,
            "Бот добавлен в беседу. Если вы хотели привязать ее к площадке или подписочной рекламе, "
            "начните нужный сценарий в личке с ботом и добавьте бота в беседу повторно.\n\n"
            f"peer_id этой беседы: {chat_peer_id}",
        )
        return True

    try:
        group_name = await _save_chat_meta(ctx, chat_peer_id)
        if state == "partner_group_id":
            group = await _create_access_chat_group(ctx, chat_peer_id, group_name)
            ctx.clear_state()
            await _answer_private_safely(
                ctx,
                f"Беседа доступа добавлена: {group_name}.\n\n"
                "Теперь выберите режим доступа: бесплатно по подпискам, платно по времени или платно по сообщениям.",
            )
            if group:
                await _show_partner_group_rates(ctx, int(group["id"]))
            return True

        if state == "buy_group":
            await _show_buy_group_partner_selector(ctx, chat_peer_id, group_name, private=True)
            return True

        if state == "partner_group_need_groups":
            group_db_id = int(ctx.data.get("need_group_partner_db_id") or 0)
            is_admin_choice = bool(ctx.data.get("need_group_is_admin_choice"))
            if not group_db_id:
                ctx.clear_state()
                await _answer_private_safely(ctx, "Не нашел площадку для настройки. Откройте ее заново в личке.")
                return True

            async with PartnerGroups() as partner_groups:
                group = await partner_groups.get_by_db_id(group_db_id)
                if not group:
                    ctx.clear_state()
                    await _answer_private_safely(ctx, "Площадка не найдена.")
                    return True
                need_group_ids = [int(group_id) for group_id in (group["need_groups"] or [])]
                if chat_peer_id not in need_group_ids:
                    need_group_ids.append(chat_peer_id)
                await partner_groups.replace_group_need_groups(int(group["group_id"]), need_group_ids)

            ctx.clear_state()
            ctx.update_data(is_admin_choice=is_admin_choice)
            await _answer_private_safely(
                ctx,
                f"Беседа добавлена в условия подписки: {group_name}.\n"
                f"Всего обязательных бесед: {len(need_group_ids)}.",
                keyboard=kb.keyboard([[kb.text_button("Открыть площадку", "open_partner_group", "primary", item_id=group_db_id)]]),
            )
            return True

        await _answer_private_safely(
            ctx,
            "Бот добавлен в беседу, но сейчас в личке нет сценария, который ждет VK-беседу.\n\n"
            f"peer_id этой беседы: {chat_peer_id}",
        )
        return True
    except Exception:
        logger.exception("Failed to complete VK chat binding user=%s peer_id=%s state=%s", ctx.user_id, chat_peer_id, state)
        await _answer_private_safely(ctx, "Не смог привязать беседу. Проверьте, что бот добавлен в нее и попробуйте еще раз.")
        return True


def _sub_rate_type(group: dict | Any) -> str:
    return str(group.get("sub_rate_type") if isinstance(group, dict) else group["sub_rate_type"] or "none").strip() or "none"


def _sub_rates(group: dict | Any) -> dict[str, list[dict[str, int]]]:
    raw = group.get("sub_rates") if isinstance(group, dict) else group["sub_rates"]
    return normalize_sub_rates(raw)


def _rate_label(rate_type: str, rate: dict[str, int]) -> str:
    if rate_type == "msg":
        return f"{int(rate['msg'])} сообщений - {int(rate['price_rub'])} ₽"
    days = int(rate["days"])
    if days % 365 == 0:
        period = f"{days // 365} год" if days == 365 else f"{days // 365} лет"
    elif days % 30 == 0:
        period = f"{days // 30} мес."
    else:
        period = f"{days} дн."
    return f"{period} - {int(rate['price_rub'])} ₽"


def _format_access_rates(group: dict | Any) -> str:
    rate_type = _sub_rate_type(group)
    rates = _sub_rates(group)
    type_text = {"none": "без оплаты", "time": "по времени", "msg": "по количеству сообщений"}.get(rate_type, rate_type)
    lines = [f"Текущий режим: {type_text}", ""]
    for current_type, title in (("time", "Тарифы по времени"), ("msg", "Тарифы по сообщениям")):
        lines.append(title + ":")
        values = rates.get(current_type) or []
        if not values:
            lines.append("- не настроены")
        for index, rate in enumerate(values, 1):
            lines.append(f"{index}. {_rate_label(current_type, rate)}")
        lines.append("")
    return "\n".join(lines).strip()


def _parse_access_rates(raw: str, rate_type: str) -> list[dict[str, int]] | None:
    rates = []
    for line in raw.splitlines():
        clean = line.replace("=", " ").replace(",", " ").strip()
        if not clean:
            continue
        parts = clean.split()
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        amount, price = map(int, parts)
        if amount <= 0 or price <= 0:
            return None
        if rate_type == "msg":
            rates.append({"msg": amount, "price_rub": price})
        else:
            rates.append({"days": amount, "price_rub": price})
    return rates or None


async def _ensure_user(ctx: Ctx) -> None:
    is_admin = await _is_admin(ctx)
    referral_user_id = None
    ref = ctx.ref
    if ref and str(ref).lstrip("-").isdigit():
        async with PartnerGroups() as partner_groups:
            referral_user_id = await partner_groups.get_creator_id(int(ref))

    async with Users() as users:
        if not await users.in_db(ctx.user_id):
            await users.add(
                ctx.user_id,
                status=UserStatus.ADMIN if is_admin else UserStatus.NO_ROLE,
                referral_user_id=referral_user_id,
            )


async def _subscription_groups_for_api(api: VKApi, main_group_id: int) -> list[tuple[int, str, str | None]]:
    async with PartnerGroups() as partner_groups:
        need_group_ids = await partner_groups.get_active_need_group_ids(main_group_id)
        if not need_group_ids and is_vk_chat_peer_id(main_group_id):
            async with AdGroups() as ad_groups:
                active_ad_groups = await ad_groups.get_by_status(AdGroupsStatus.ACTIVE)
            if any(int(group["group_id"]) == int(main_group_id) for group in active_ad_groups):
                linked_partner_groups = await partner_groups.get_by_ad_group_id(main_group_id)
                need_group_ids = [
                    int(group["group_id"])
                    for group in linked_partner_groups
                    if int(group["partner_type"]) != int(PartnerTypes.FROZEN)
                ]
    groups = []
    for group_id in need_group_ids:
        link = None
        try:
            link = await api.target_link(group_id)
        except Exception:
            logger.exception("Failed to build VK target link for group=%s", group_id)
        groups.append((group_id, await api.group_title(group_id), link))
    return groups


async def _subscription_groups(ctx: Ctx, main_group_id: int) -> list[tuple[int, str, str | None]]:
    return await _subscription_groups_for_api(ctx.api, main_group_id)


async def _is_group_member_for_api(api: VKApi, group_id: int, user_id: int) -> bool:
    if is_vk_chat_peer_id(group_id):
        return await api.is_chat_member(group_id, user_id)

    async with VkGroups() as vk_groups:
        meta = await vk_groups.get(abs(int(group_id)))
    token = meta and meta["token"]
    if token and abs(int(group_id)) != abs(int(api.group_id or 0)):
        group_api = VKApi(token, group_id=abs(int(group_id)), api_version=api.api_version)
        try:
            return await group_api.is_group_member(group_id, user_id)
        except Exception as exc:
            logger.warning("Stored VK token failed for membership check group=%s; falling back: %s", group_id, exc)
        finally:
            await group_api.close()
    return await api.is_group_member(group_id, user_id)


async def _is_group_member(ctx: Ctx, group_id: int, user_id: int) -> bool:
    return await _is_group_member_for_api(ctx.api, group_id, user_id)


async def _missing_subscription_groups(api: VKApi, main_group_id: int, user_id: int) -> list[tuple[int, str, str | None]]:
    missing = []
    for group_id, title, link in await _subscription_groups_for_api(api, main_group_id):
        try:
            if not await _is_group_member_for_api(api, group_id, user_id):
                missing.append((group_id, title, link))
        except Exception:
            logger.exception("Failed to check VK membership for user=%s group=%s", user_id, group_id)
            missing.append((group_id, title, link))
    return missing


async def _paid_access_status(group: Any, user_id: int) -> tuple[bool, str]:
    rate_type = _sub_rate_type(group)
    if rate_type not in ("time", "msg"):
        return True, ""

    group_id = int(group["group_id"])
    async with UsersSubs() as subs:
        sub = await subs.get_sub(user_id, group_id)
        if not sub or str(sub["type"]).strip() != rate_type:
            return False, "not_paid"
        if rate_type == "time":
            expires_at = sub["expires_at"]
            if expires_at and expires_at > get_msk_now():
                return True, ""
            await subs.remove_sub(user_id, group_id)
            return False, "expired"
        msg_left = sub["msg_left"]
        if msg_left is not None and int(msg_left) > 0:
            return True, ""
        await subs.remove_sub(user_id, group_id)
        return False, "messages_left"


async def _access_group_for_id(group_id: int) -> Any | None:
    async with PartnerGroups() as partner_groups:
        return await partner_groups.get_by_group_id(group_id)


async def _consume_paid_message(group: Any, user_id: int) -> None:
    if _sub_rate_type(group) != "msg":
        return
    group_id = int(group["group_id"])
    async with UsersSubs() as subs:
        msg_left = await subs.reduce_msg_left(user_id, group_id)
        if msg_left is not None and int(msg_left) <= 0:
            await subs.remove_sub(user_id, group_id)


def _paid_access_text(group: Any, reason: str = "not_paid") -> str:
    title = "Для размещения на этой площадке нужно купить доступ."
    if reason == "expired":
        title = "Оплаченный доступ по времени закончился."
    elif reason == "messages_left":
        title = "Оплаченные сообщения закончились."
    rate_type = _sub_rate_type(group)
    rates = _sub_rates(group).get(rate_type) or []
    lines = [title, "", "Доступные тарифы:"]
    for index, rate in enumerate(rates, 1):
        lines.append(f"{index}. {_rate_label(rate_type, rate)}")
    return "\n".join(lines)


def _paid_access_kb(group: Any) -> str:
    rate_type = _sub_rate_type(group)
    rows = []
    for index, rate in enumerate((_sub_rates(group).get(rate_type) or [])[:5]):
        rows.append([kb.text_button(_rate_label(rate_type, rate)[:40], "buy_group_access", "primary", main_group_id=int(group["group_id"]), rate_index=index)])
    return kb.keyboard(rows)


async def _send_paid_access_prompt(ctx: Ctx, group: Any, reason: str = "not_paid") -> bool:
    if _sub_rate_type(group) not in ("time", "msg"):
        return False
    text = _paid_access_text(group, reason)
    if ctx.is_chat:
        text = (
            f"Привет {await _vk_user_mention(ctx.api, ctx.user_id)}. Рад видеть тебя в группе, "
            "чтобы размещать объявления, необходимо зарегистрироваться через наш бот по кнопке\n\n"
            + text
        )
    message_id = await ctx.answer(text, keyboard=_paid_access_kb(group))
    if ctx.is_chat:
        _schedule_temporary_delete(ctx.api, ctx.peer_id, message_id)
    return True


async def _vk_user_mention(api: VKApi, user_id: int) -> str:
    try:
        name = await api.get_user_name(user_id)
    except Exception:
        logger.exception("Failed to get VK user name for mention user=%s", user_id)
        name = "User"
    return f"[id{int(user_id)}|{name or 'User'}]"


async def _subscription_partner_group(ctx: Ctx):
    if ctx.peer_id <= 2_000_000_000:
        return None

    group = await _access_group_for_id(ctx.peer_id)
    if not group:
        return None
    if group["partner_type"] in (PartnerTypes.SUB_GROUPS, PartnerTypes.PROMOTION_AND_SUB):
        return group
    return None


async def _send_subscription_prompt(ctx: Ctx, main_group_id: int, *, mode: str = "initial") -> bool:
    groups = await _subscription_groups(ctx, main_group_id)
    if not groups:
        return False
    mention = await _vk_user_mention(ctx.api, ctx.user_id)
    if mode == "resubscribe":
        text = f"{mention}\nНеобходимо подписаться на группы и не отписываться"
    else:
        text = f"Привет {mention}. Для того чтобы написать сообщения, необходимо подписаться на следующие каналы"
    message_id = await ctx.answer(
        text,
        keyboard=kb.subscription_check_kb(groups, main_group_id),
    )
    if ctx.is_chat:
        _schedule_temporary_delete(ctx.api, ctx.peer_id, message_id)
    return True


async def _check_and_grant_subscription(ctx: Ctx, main_group_id: int) -> bool:
    partner_group = await _access_group_for_id(main_group_id)
    if partner_group:
        has_paid_access, reason = await _paid_access_status(partner_group, ctx.user_id)
        if not has_paid_access:
            await _send_paid_access_prompt(ctx, partner_group, reason)
            return False

    groups = await _subscription_groups(ctx, main_group_id)
    if not groups:
        message_id = await ctx.answer("Для этой площадки сейчас нет активных подписочных условий или доступ уже выдан.")
        if ctx.is_chat:
            _schedule_temporary_delete(ctx.api, ctx.peer_id, message_id)
        return False

    missing = await _missing_subscription_groups(ctx.api, main_group_id, ctx.user_id)

    if missing:
        await _send_subscription_prompt(ctx, main_group_id)
        return False

    async with UsersSubs() as subs:
        await subs.add_sub(ctx.user_id, main_group_id, "sub_msg")
    await add_counter(EventType.SUB_BUTTON_PRESSED)
    message_id = await ctx.answer("Подписка подтверждена. Доступ выдан.")
    if ctx.is_chat:
        _schedule_temporary_delete(ctx.api, ctx.peer_id, message_id)
    return True


async def _chat_guard(ctx: Ctx) -> bool:
    partner_group = await _subscription_partner_group(ctx)
    if not partner_group:
        return False
    source_group_id = int(partner_group["group_id"])

    has_paid_access, paid_reason = await _paid_access_status(partner_group, ctx.user_id)
    if not has_paid_access:
        message_id = ctx.message.get("id")
        conversation_message_id = ctx.message.get("conversation_message_id")
        if message_id or conversation_message_id:
            try:
                await ctx.api.delete_message(
                    int(message_id or 0),
                    delete_for_all=True,
                    peer_id=ctx.peer_id,
                    conversation_message_id=int(conversation_message_id or 0) or None,
                )
            except Exception:
                logger.exception("Failed to delete message %s in VK chat", message_id)
        await _send_paid_access_prompt(ctx, partner_group, paid_reason)
        return False

    groups = await _subscription_groups(ctx, source_group_id)
    if not groups:
        await _consume_paid_message(partner_group, ctx.user_id)
        return True

    async with UsersSubs() as subs:
        existing_sub = await subs.get_sub(ctx.user_id, source_group_id)
    missing = await _missing_subscription_groups(ctx.api, source_group_id, ctx.user_id)
    if missing:
        message_id = ctx.message.get("id")
        conversation_message_id = ctx.message.get("conversation_message_id")
        if message_id or conversation_message_id:
            try:
                await ctx.api.delete_message(
                    int(message_id or 0),
                    delete_for_all=True,
                    peer_id=ctx.peer_id,
                    conversation_message_id=int(conversation_message_id or 0) or None,
                )
            except Exception:
                logger.exception("Failed to delete message %s in VK chat", message_id)
        await _send_subscription_prompt(ctx, source_group_id, mode="resubscribe" if existing_sub else "initial")
        return False

    async with UsersSubs() as subs:
        await subs.add_sub(ctx.user_id, source_group_id, "sub_msg")
    await _consume_paid_message(partner_group, ctx.user_id)
    return True


async def _show_partner_group(ctx: Ctx, group_db_id: int, is_admin_choice: bool = False) -> None:
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(group_db_id)
    if not group:
        await ctx.answer("Площадка не найдена.")
        return
    if not is_admin_choice and group["creator_id"] != ctx.user_id:
        await ctx.answer("Нет доступа к этой площадке.")
        return
    title = await ctx.api.group_title(group["group_id"])
    await ctx.answer(
        texts.partner_group_text(group, title),
        keyboard=kb.manage_partner_group_kb(group_db_id, group["partner_type"], is_admin_choice),
    )


async def _show_partner_group_rates(ctx: Ctx, group_db_id: int, is_admin_choice: bool = False) -> None:
    async with PartnerGroups() as partner_groups:
        group = await partner_groups.get_by_db_id(group_db_id)
    if not group:
        await ctx.answer("Площадка не найдена.")
        return
    rows = [
        [
            kb.text_button("Без оплаты", "partner_group_rate_type.none", "primary", group_id=group_db_id),
            kb.text_button("По времени", "partner_group_rate_type.time", "primary", group_id=group_db_id),
            kb.text_button("По сообщениям", "partner_group_rate_type.msg", "primary", group_id=group_db_id),
        ],
        [
            kb.text_button("Ред. время", "partner_group_edit_rates", group_id=group_db_id, rate_type="time"),
            kb.text_button("Ред. сообщения", "partner_group_edit_rates", group_id=group_db_id, rate_type="msg"),
        ],
        [kb.text_button("Назад", "open_partner_group", "negative", item_id=group_db_id)],
    ]
    ctx.update_data(is_admin_choice=is_admin_choice)
    await ctx.answer(_format_access_rates(group), keyboard=kb.keyboard(rows))


async def _show_poster(ctx: Ctx, poster_id: int) -> None:
    async with Posters() as posters:
        poster = await posters.get_by_id(poster_id)
    if not poster:
        await ctx.answer("Объявление не найдено.", keyboard=kb.back("manage_ad_posts"))
        return
    await ctx.answer(
        texts.admin_poster_text(poster),
        keyboard=kb.poster_admin_kb(poster["id"], poster["status"]),
        attachment=poster["file_id"],
    )


async def _show_ad_group(ctx: Ctx, ad_group_db_id: int) -> None:
    async with AdGroups() as ad_groups:
        ad_group = await ad_groups.get_by_db_id(ad_group_db_id)
    if not ad_group:
        await ctx.answer("Рекламная группа не найдена.", keyboard=kb.back("manage_ad_groups"))
        return
    title = await ctx.api.group_title(ad_group["group_id"])
    await ctx.answer(texts.admin_ad_group_text(ad_group, title), keyboard=kb.ad_group_admin_kb(ad_group["id"], ad_group["status"]))


async def _poster_target_groups(poster_id: int) -> tuple[Any, list[Any]]:
    async with Posters() as posters:
        poster = await posters.get_by_id(poster_id)
    if not poster:
        return None, []

    groups_data = await get_suitable_groups(poster)
    all_groups = []
    seen = set()
    for group in groups_data["suitable_groups"] + groups_data["selected_groups"]:
        if group["group_id"] not in seen:
            seen.add(group["group_id"])
            all_groups.append(group)
    return poster, all_groups


async def _show_poster_schedule_groups(ctx: Ctx, poster_id: int) -> None:
    poster, all_groups = await _poster_target_groups(poster_id)
    if not poster:
        await ctx.answer("Объявление не найдено.", keyboard=kb.back("manage_ad_posts"))
        return
    if not all_groups:
        await ctx.answer("Нет подходящих площадок для этого объявления.", keyboard=kb.back("manage_ad_posts"))
        return

    values = [group["group_id"] for group in all_groups]
    text = ["Выберите площадку для отправки:"]
    for index, group in enumerate(all_groups, 1):
        text.append(f"{index}) {await ctx.api.group_title(group['group_id'])}")
    text.append("")
    text.append("Можно нажать кнопку с номером или отправить номер сообщением.")
    ctx.update_data(schedule_poster_id=poster_id, schedule_group_values=values)
    ctx.set_state("poster_schedule_group")
    await ctx.answer("\n".join(text), keyboard=kb.number_action_kb(len(values), "poster_schedule_group", "manage_ad_posts"))


async def _select_poster_schedule_group(ctx: Ctx, raw_num: str) -> None:
    values = ctx.data.get("schedule_group_values") or []
    if not raw_num.isdigit() or int(raw_num) < 1 or int(raw_num) > len(values):
        await ctx.answer("Неверный номер площадки.")
        return
    group_id = int(values[int(raw_num) - 1])
    ctx.update_data(schedule_group_id=group_id)
    await ctx.answer("Введите время отправки по МСК в формате 18:00.")
    ctx.set_state("poster_schedule_time")


def _parse_hh_mm(raw_value: str) -> time | None:
    parts = raw_value.replace(".", ":").split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hour, minute = map(int, parts)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_dd_mm_yyyy(raw_value: str) -> date | None:
    parts = raw_value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts) or len(parts[2]) != 4:
        return None
    day, month, year = map(int, parts)
    try:
        return date(year, month, day)
    except ValueError:
        return None


async def _show_newsletter(ctx: Ctx, nl_id: int) -> None:
    async with Newsletters() as newsletters:
        nl = await newsletters.get_by_id(nl_id)
    if not nl or not nl["expires_at"]:
        await ctx.answer("Рассылка не найдена.")
        return

    send_time = nl["send_time"].strftime("%H:%M") if nl["send_time"] else "15:00"
    await ctx.answer(
        f"Рассылка #{nl['id']}\n"
        f"Автор: {nl['creator_id']}\n"
        f"До: {nl['expires_at'].strftime('%d.%m.%Y')}\n"
        f"Время публикации: {send_time} МСК\n\n"
        f"{nl['text']}",
        keyboard=kb.keyboard(
            [
                [kb.text_button("Принять", "newsletter_act.apply", "positive", nl_id=nl_id)],
                [
                    kb.text_button("Время публикации", "newsletter_change_time", nl_id=nl_id),
                    kb.text_button("Дата окончания", "newsletter_change_expires", nl_id=nl_id),
                ],
                [kb.text_button("Отклонить", "newsletter_act.delete", "negative", nl_id=nl_id)],
                [kb.text_button("Назад", "newsletter_moderation", "negative")],
            ]
        ),
        attachment=nl["file_id"],
    )


async def _show_partner_statistics(ctx: Ctx, cursor: int = 0) -> None:
    async with Partners() as partners:
        partners_info = await partners.get_all()
    if not partners_info:
        await ctx.answer("На данный момент в боте нет партнеров.", keyboard=kb.back("statistics"))
        return

    cursor = max(0, min(cursor, len(partners_info) - 1))
    ctx.update_data(partner_stats_cursor=cursor)
    await ctx.answer(
        await texts.partner_stats_text(ctx.api, partners_info[cursor]),
        keyboard=kb.partner_stats_kb(len(partners_info), cursor),
    )


async def _show_admin_newsletters_statistics(ctx: Ctx, offset: int = 0) -> None:
    page_size = 5
    async with Newsletters() as newsletters:
        nls = await newsletters.get_all()
    if not nls:
        await ctx.answer("На данный момент нет рассылок от администратора.", keyboard=kb.back("newsletter_statistics"))
        return

    offset = max(0, min(offset, max(0, len(nls) - 1)))
    offset -= offset % page_size
    ctx.update_data(admin_newsletters_offset=offset)
    await ctx.answer(
        texts.admin_nl_text(nls[offset : offset + page_size]),
        keyboard=kb.admin_newsletters_page_kb(len(nls), offset, page_size),
    )


async def _show_advert_newsletter_statistics(ctx: Ctx, cursor: int = 0) -> None:
    async with Newsletters() as newsletters:
        nls = await newsletters.get_all(is_sub=True)
    if not nls:
        await ctx.answer("На данный момент нет рассылок от рекламодателей.", keyboard=kb.back("newsletter_statistics"))
        return

    cursor = max(0, min(cursor, len(nls) - 1))
    current_nl = nls[cursor]
    ctx.update_data(advert_newsletter_cursor=cursor, advert_newsletter_id=int(current_nl["id"]))
    await ctx.answer(
        await texts.advert_nl_text(ctx.api, nls, cursor),
        keyboard=kb.advert_newsletter_kb(len(nls), cursor, bool(current_nl["is_moderating"])),
        attachment=current_nl["file_id"],
    )


def register_handlers(app: VKBotApp) -> None:
    @app.default
    async def default(ctx: Ctx) -> None:
        if await _maybe_complete_chat_binding(ctx):
            return
        if await _chat_guard(ctx):
            return
        if ctx.peer_id > 2_000_000_000:
            return
        await start(ctx)

    @app.command("start")
    @app.command("main_menu")
    async def start(ctx: Ctx) -> None:
        ctx.clear_state()
        await _ensure_user(ctx)
        if ctx.ref and str(ctx.ref).lstrip("-").isdigit():
            ref_group_id = int(ctx.ref)
            partner_group = await _access_group_for_id(ref_group_id)
            if partner_group:
                has_paid_access, paid_reason = await _paid_access_status(partner_group, ctx.user_id)
                if not has_paid_access and await _send_paid_access_prompt(ctx, partner_group, paid_reason):
                    return
            if await _send_subscription_prompt(ctx, ref_group_id):
                return
        await ctx.answer(texts.WELCOME_TEXT, keyboard=kb.main_menu(await _is_admin(ctx)))

    @app.command("check_subs")
    async def check_subs(ctx: Ctx) -> None:
        main_group_id = int(ctx.payload.get("main_group_id") or ctx.ref or 0)
        if not main_group_id:
            await ctx.answer("Не понял, для какой площадки проверять подписку.")
            return
        await _check_and_grant_subscription(ctx, main_group_id)

    @app.command("buy_group_access")
    async def buy_group_access(ctx: Ctx) -> None:
        main_group_id = int(ctx.payload.get("main_group_id") or 0)
        rate_index = int(ctx.payload.get("rate_index") or 0)
        group = await _access_group_for_id(main_group_id)
        if not group or _sub_rate_type(group) not in ("time", "msg"):
            await ctx.answer("Для этой площадки сейчас нет платных тарифов.")
            return
        rate_type = _sub_rate_type(group)
        rates = _sub_rates(group).get(rate_type) or []
        if rate_index < 0 or rate_index >= len(rates):
            await ctx.answer("Тариф не найден.")
            return
        rate = rates[rate_index]
        price = int(rate["price_rub"])
        ctx.clear_state()
        ctx.update_data(
            ad_type="sub_access",
            from_user=ctx.user_id,
            sub_period=int(rate.get("days") or rate.get("msg") or 0),
            current_pay_info={"sum": price},
            access_group_id=main_group_id,
            access_rate_type=rate_type,
            access_rate=rate,
        )
        await ctx.answer(
            f"Выбран тариф: {_rate_label(rate_type, rate)}\n\n"
            f"К оплате: {price} ₽.\n\n"
            "YooMoney будет подключен последним этапом, сейчас покупка оформляется через ручное подтверждение.\n"
            "Отправьте сюда комментарий к платежу или скриншот. Админ подтвердит заявку, после чего доступ активируется."
        )
        ctx.set_state("pay_details")

    @app.command("menu_advertiser")
    async def advertiser_menu(ctx: Ctx) -> None:
        await _ensure_user(ctx)
        await ctx.answer(texts.ADVERTISER_MENU_TEXT, keyboard=kb.advertiser_menu())

    @app.command("menu_partner")
    async def partner_menu(ctx: Ctx) -> None:
        await _ensure_user(ctx)
        await ctx.answer(texts.NEW_PARTNER_MENU_TEXT, keyboard=kb.partner_menu())

    @app.command("menu_adminpanel")
    async def admin_menu(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(texts.ADMIN_MENU_TEXT, keyboard=kb.admin_menu())

    @app.command("paid_plains")
    async def paid_plains(ctx: Ctx) -> None:
        await ctx.answer(texts.ad_rates_text(), keyboard=kb.back("menu_advertiser"))

    @app.command("buy_ad")
    async def buy_ad(ctx: Ctx) -> None:
        await ctx.answer("Выберите вариант рекламы:", keyboard=kb.choose_ad_type())

    @app.command_prefix("buy_ad.")
    async def buy_ad_type(ctx: Ctx) -> None:
        ad_type = ctx.cmd.split(".")[-1]
        ctx.clear_state()
        ctx.update_data(ad_type=ad_type, from_user=ctx.user_id)
        if ad_type == "poster":
            await ctx.answer(texts.BUY_POSTER_AD_TEXT.format(texts.categories_text()), keyboard=kb.back("buy_ad"))
            ctx.set_state("buy_poster")
        elif ad_type == "group":
            await ctx.answer(texts.BUY_GROUP_AD_TEXT, keyboard=kb.back("buy_ad"))
            ctx.set_state("buy_group")
        elif ad_type == "newsletter":
            await ctx.answer(texts.BUY_NEWSLETTER_AD_TEXT, keyboard=kb.back("buy_ad"))
            ctx.set_state("buy_newsletter")

    @app.state_handler("buy_group")
    async def buy_group_state(ctx: Ctx) -> None:
        if "token=" in ctx.text.casefold() or "vk1." in ctx.text:
            await ctx.answer("Ключи здесь не нужны. Укажите peer_id VK-беседы, например 2000001234.")
            return
        chat_peer_id = _parse_vk_chat_peer_id(ctx.text)
        if not chat_peer_id:
            await ctx.answer(
                "Укажите peer_id VK-беседы, где бот состоит участником. "
                "Это число вида 2000001234, его можно получить командой /id в нужной беседе."
            )
            return
        try:
            group_name = await ctx.api.chat_title(chat_peer_id)
            if not await ctx.api.is_chat_member(chat_peer_id, ctx.user_id):
                await ctx.answer("Вы не состоите в этой VK-беседе, поэтому я не могу принять ее для подписочной рекламы.")
                return
        except Exception:
            await ctx.answer("Не смог получить доступ к VK-беседе. Добавьте бота в беседу и повторите /id.")
            return

        async with VkGroups() as vk_groups:
            await vk_groups.upsert(
                chat_peer_id,
                title=group_name,
                screen_name="",
                target_type="chat",
            )
        await _show_buy_group_partner_selector(ctx, chat_peer_id, group_name)

    @app.command("select_group")
    async def select_group(ctx: Ctx) -> None:
        values = ctx.data.get("select_group_values") or []
        selected = _selected(ctx.data)
        num = str(ctx.payload.get("num") or "")
        if not num.isdigit() or int(num) < 1 or int(num) > len(values):
            await ctx.answer("Неверный номер.")
            return
        selected.remove(num) if num in selected else selected.add(num)
        ctx.update_data(selected=selected)
        confirm = ctx.data.get("select_confirm_cmd", "confirm_select_groups")
        back_cmd = ctx.data.get("select_back_cmd", "buy_ad")
        await ctx.answer("Обновил выбор.", keyboard=kb.number_select_kb(len(values), selected, confirm, back_cmd))

    @app.command("confirm_select_groups")
    async def confirm_select_groups(ctx: Ctx) -> None:
        selected = _selected(ctx.data)
        values = ctx.data.get("select_group_values") or []
        if not selected:
            await ctx.answer("Не выбрана ни одна площадка.")
            return
        selected_group_ids = [values[int(num) - 1] for num in sorted(selected, key=int)]
        ctx.update_data(selected_group_ids=selected_group_ids)
        await ctx.answer("Теперь напишите количество дней покупки, например 8.")
        ctx.set_state("buy_ad_period")

    @app.state_handler("buy_poster")
    async def buy_poster_state(ctx: Ctx) -> None:
        if "\n" not in ctx.text:
            await ctx.answer("Нужно две строки: первая - тематика, дальше текст рекламы.")
            return
        ad_topic, poster_text = ctx.text.split("\n", 1)
        category_values = [value.lower() for value in get_ad_categories().values()]
        if ad_topic.strip().lower() not in category_values:
            await ctx.answer("Такой тематики нет в списке. Проверьте первую строку.")
            return
        topic_id = category_values.index(ad_topic.strip().lower()) + 1
        ctx.update_data(
            poster_info={
                "ad_topic_id": topic_id,
                "msg": poster_text.strip(),
                "attachment": ctx.attachment,
            }
        )
        await ctx.answer(texts.SELECT_REGION_AD_TEXT)
        ctx.set_state("buy_region")

    @app.state_handler("buy_region")
    async def buy_region_state(ctx: Ctx) -> None:
        regions = _validate_regions(ctx.text)
        if regions is None:
            await ctx.answer("Регионы указаны неверно. Пример: 77 78 или 0.")
            return
        ctx.update_data(region_codes=regions)
        await ctx.answer(texts.ADD_POSTER_SUCCESSFUL_TEXT.format(", ".join(get_regions().get(region, region) for region in regions)))
        ctx.set_state("buy_ad_period")

    @app.state_handler("buy_newsletter")
    async def buy_newsletter_state(ctx: Ctx) -> None:
        if not ctx.text:
            await ctx.answer("Введите текст рассылки.")
            return
        ctx.update_data(newsletter_text=ctx.text, newsletter_attachment=ctx.attachment)
        await ctx.answer("Выберите цель рассылки:", keyboard=kb.newsletter_targets())
        ctx.set_state(None)

    @app.command_prefix("ad_newsletter_target.")
    async def ad_newsletter_target(ctx: Ctx) -> None:
        target = ctx.cmd.split(".")[-1]
        ctx.update_data(newsletter_target=target)
        await ctx.answer(texts.ADD_NEWSLETTER_SUCCESSFUL_TEXT)
        ctx.set_state("buy_ad_period")

    @app.state_handler("buy_ad_period")
    async def buy_period_state(ctx: Ctx) -> None:
        if not ctx.text.isdigit() or int(ctx.text) <= 0:
            await ctx.answer("Период должен быть положительным числом.")
            return
        period = int(ctx.text)
        ad_type = ctx.data["ad_type"]
        price = texts.calc_price(ad_type, period)
        if not price:
            await ctx.answer("На этот период тариф недоступен.")
            return
        ctx.update_data(sub_period=period, current_pay_info={"sum": price})
        await ctx.answer(texts.payment_instruction(period, price))
        ctx.set_state("pay_details")

    @app.state_handler("pay_details")
    async def pay_details_state(ctx: Ctx) -> None:
        state = _payment_state_from_data(ctx.data)
        async with ManualPayments() as payments:
            pay_id = await payments.add(state)
        await send_log(
            ctx.api,
            texts.manual_pay_request_text(state["current_pay_info"]["sum"], ctx.text, ctx.user_id, state["ad_type"]),
            keyboard=kb.manual_payment_kb(pay_id),
            attachment=ctx.attachment,
        )
        await ctx.answer("Ваш запрос отправлен на обработку.", keyboard=kb.advertiser_menu())
        ctx.clear_state()

    @app.command("my_ads")
    async def my_ads(ctx: Ctx) -> None:
        async with AdGroups() as ad_groups:
            active = await ad_groups.get_by_status(AdGroupsStatus.ACTIVE, ctx.user_id)
        titles = await _group_titles(ctx.api, [group["group_id"] for group in active])
        await ctx.answer(await texts.my_ads_text(ctx.user_id, titles), keyboard=kb.back("menu_advertiser"))

    @app.command("add_bot_group")
    async def add_bot_group(ctx: Ctx) -> None:
        await ctx.answer(texts.ADD_PARTNER_GROUP, keyboard=kb.back("menu_partner"))
        ctx.set_state("partner_group_id")

    @app.state_handler("partner_group_id")
    async def partner_group_id_state(ctx: Ctx) -> None:
        chat_peer_id = _parse_vk_chat_peer_id(ctx.text)
        if chat_peer_id:
            try:
                group_name = await ctx.api.chat_title(chat_peer_id)
                if not await ctx.api.is_chat_member(chat_peer_id, ctx.user_id):
                    await ctx.answer("Вы не состоите в этой VK-беседе, поэтому я не могу принять ее для настройки доступа.")
                    return
            except Exception:
                await ctx.answer("Не смог получить доступ к VK-беседе. Добавьте бота в беседу и повторите /id.")
                return

            group = await _create_access_chat_group(ctx, chat_peer_id, group_name)
            ctx.clear_state()
            await ctx.answer(
                f"Беседа доступа добавлена: {group_name}.\n\n"
                "Теперь выберите режим доступа: бесплатно по подпискам, платно по времени или платно по сообщениям."
            )
            if group:
                await _show_partner_group_rates(ctx, int(group["id"]))
            return

        group_ref, token = _extract_group_and_token(ctx.text)
        if not group_ref or not token:
            await ctx.answer(
                "Нужно отправить ссылку/ID сообщества и ключ доступа в одном сообщении.\n\n"
                "Пример: https://vk.com/club123456 token=vk1.a.xxxxx\n\n"
                "Для VK-беседы доступа отправьте peer_id вида 2000001234 без токена."
            )
            return
        try:
            group = await ctx.api.resolve_group(group_ref)
            if isinstance(ctx.api, VKApi):
                group_api = VKApi(token, group_id=group.id, api_version=ctx.api.api_version)
                try:
                    await group_api.group_title(group.id)
                finally:
                    await group_api.close()
            group_id = group.id
            group_name = group.name
            group_screen = group.screen_name
        except Exception:
            logger.exception("Failed to resolve VK community or validate token")
            await ctx.answer("Не смог проверить сообщество или токен. Проверьте ссылку, токен и права доступа к стене.")
            return
        async with VkGroups() as vk_groups:
            await vk_groups.upsert(
                group_id,
                title=group_name,
                screen_name=group_screen,
                token=token,
                target_type="community",
                can_wall_post=True,
            )
        ctx.update_data(partner_group_id=group_id, partner_group_token=token)
        await ctx.answer(f"Сообщество найдено: {group_name}. Токен сохранен.\n\n{texts.SELECT_REGION_PARTNER_TEXT}")
        ctx.set_state("partner_region")

    @app.state_handler("partner_region")
    async def partner_region_state(ctx: Ctx) -> None:
        regions = _validate_regions(ctx.text)
        if regions is None:
            await ctx.answer("Регионы указаны неверно. Пример: 77 78 или 0.")
            return
        ctx.update_data(region_codes=regions, selected_categories=set(), category_mode="partner_add")
        await ctx.answer(texts.SELECT_PARTNER_AD_CATEGORY_TEXT, keyboard=kb.categories_kb(get_ad_categories(), set()))
        ctx.set_state(None)

    @app.command("select_category")
    async def select_category(ctx: Ctx) -> None:
        category_id = str(ctx.payload.get("category_id"))
        selected = _selected(ctx.data, "selected_categories")
        if category_id == "0":
            selected = set() if "0" in selected else {"0"}
        else:
            selected.discard("0")
            selected.remove(category_id) if category_id in selected else selected.add(category_id)
        ctx.update_data(selected_categories=selected)
        await ctx.answer("Обновил выбор категорий.", keyboard=kb.categories_kb(get_ad_categories(), selected))

    @app.command("confirm_select_ad_categories")
    async def confirm_categories(ctx: Ctx) -> None:
        selected = _selected(ctx.data, "selected_categories")
        if not selected:
            await ctx.answer("Выберите хотя бы одну категорию.")
            return
        group_id = int(ctx.data["partner_group_id"])
        regions = list(map(int, ctx.data["region_codes"]))
        async with PartnerGroups() as partner_groups:
            await partner_groups.add(ctx.user_id, group_id, regions, list(selected))
        async with Partners() as partners:
            if not await partners.in_db(ctx.user_id):
                await partners.add(ctx.user_id)
                await add_counter(EventType.ADDED_NEW_PARTNER)
        async with Users() as users:
            if not await users.in_db(ctx.user_id):
                await users.add(ctx.user_id)
            await users.update_status(ctx.user_id, UserStatus.PARTNER)
        await ctx.answer(texts.partner_group_added_text(ctx.data["region_codes"]), keyboard=kb.partner_menu())
        ctx.clear_state()

    @app.command("partner_profile")
    async def partner_profile(ctx: Ctx) -> None:
        async with Partners() as partners:
            in_db = await partners.in_db(ctx.user_id)
        await ctx.answer(await texts.partner_profile_text(ctx.user_id) if in_db else "У вас еще нет подключенных площадок.", keyboard=kb.partner_profile_menu(in_db))

    @app.command("money_requests")
    async def money_requests(ctx: Ctx) -> None:
        async with UserRequests() as requests:
            all_requests = await requests.get_requests(ctx.user_id)
        await ctx.answer(texts.partner_requests_text(all_requests), keyboard=kb.keyboard([[kb.text_button("Создать заявку", "create_money_request", "primary")], [kb.text_button("Назад", "partner_profile", "negative")]]))

    @app.command("create_money_request")
    async def create_money_request(ctx: Ctx) -> None:
        async with Partners() as partners:
            partner = await partners.get_user(ctx.user_id)
        min_amount = int(getenv("MIN_WITHDRAWAL_AMOUNT", "100"))
        if not partner or partner["balance"] < min_amount:
            await ctx.answer(f"Можно выводить только от {min_amount} ₽.")
            return
        await ctx.answer("Введите сумму вывода:")
        ctx.set_state("money_amount")

    @app.state_handler("money_amount")
    async def money_amount_state(ctx: Ctx) -> None:
        if not ctx.text.isdigit():
            await ctx.answer("Введите сумму числом.")
            return
        amount = int(ctx.text)
        async with Partners() as partners:
            partner = await partners.get_user(ctx.user_id)
        if partner["balance"] < amount:
            await ctx.answer(f"Недостаточно средств. Текущий баланс: {partner['balance']} ₽.")
            return
        ctx.update_data(req_amount=amount)
        await ctx.answer(texts.REQUEST_PAY_DETAILS_TEXT)
        ctx.set_state("money_details")

    @app.state_handler("money_details")
    async def money_details_state(ctx: Ctx) -> None:
        amount = int(ctx.data["req_amount"])
        async with UserRequests() as requests:
            await requests.add(ctx.user_id, RequestType.WITHDRAWAL, amount=amount, comment=ctx.text)
            all_requests = await requests.get_requests(ctx.user_id)
        async with Partners() as partners:
            await partners.update_balance(ctx.user_id, -amount)
        await send_log(ctx.api, f"Новая заявка на вывод от {ctx.user_id}: {amount} ₽\n{ctx.text}")
        await ctx.answer(texts.partner_requests_text(all_requests), keyboard=kb.partner_profile_menu(True))
        ctx.clear_state()

    @app.command("manage_partner_groups")
    @app.command("manage_partner_groups_admin")
    async def manage_partner_groups(ctx: Ctx) -> None:
        is_admin_choice = ctx.cmd == "manage_partner_groups_admin"
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            groups = await partner_groups.get_all(creator_id=None if is_admin_choice else ctx.user_id, status=None)
        if not groups:
            await ctx.answer("Площадок нет.", keyboard=kb.back("menu_adminpanel" if is_admin_choice else "partner_profile"))
            return
        items = []
        for group in groups:
            items.append({"id": group["id"], "title": await ctx.api.group_title(group["group_id"])})
        ctx.update_data(is_admin_choice=is_admin_choice)
        await ctx.answer("Площадки:", keyboard=kb.list_select_kb(items, "open_partner_group", "menu_adminpanel" if is_admin_choice else "partner_profile"))

    @app.command("open_partner_group")
    async def open_partner_group(ctx: Ctx) -> None:
        await _show_partner_group(ctx, int(ctx.payload["item_id"]), bool(ctx.data.get("is_admin_choice")))

    @app.command("partner_group_need_groups")
    async def partner_group_need_groups(ctx: Ctx) -> None:
        group_db_id = int(ctx.payload["group_id"])
        is_admin_choice = bool(ctx.data.get("is_admin_choice"))
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
        if not group:
            await ctx.answer("Площадка не найдена.")
            return
        if not is_admin_choice and group["creator_id"] != ctx.user_id:
            if await _is_admin(ctx):
                is_admin_choice = True
            else:
                await ctx.answer("Нет доступа к этой площадке.")
                return

        current = []
        for group_id in group["need_groups"] or []:
            current.append(f"- {await ctx.api.group_title(group_id)} ({int(group_id)})")
        text = [
            "Добавьте бота в VK-беседу, в которую пользователь должен вступить перед сообщением.",
            "После добавления я продолжу настройку здесь, в личке.",
            "Если бот уже добавлен, можно отправить один или несколько peer_id через пробел/перенос строки.",
            "Чтобы очистить список, отправьте 0.",
            "",
            "Текущий список:",
            *(current or ["- пусто"]),
        ]
        ctx.update_data(need_group_partner_db_id=group_db_id, need_group_is_admin_choice=is_admin_choice)
        ctx.set_state("partner_group_need_groups")
        await ctx.answer(
            "\n".join(text),
            keyboard=kb.keyboard([[kb.text_button("Назад", "open_partner_group", "negative", item_id=group_db_id)]]),
        )

    @app.state_handler("partner_group_need_groups")
    async def partner_group_need_groups_state(ctx: Ctx) -> None:
        group_db_id = int(ctx.data.get("need_group_partner_db_id") or 0)
        is_admin_choice = bool(ctx.data.get("need_group_is_admin_choice"))
        if not group_db_id:
            ctx.clear_state()
            await ctx.answer("Не нашел площадку для настройки.")
            return
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
        if not group:
            ctx.clear_state()
            await ctx.answer("Площадка не найдена.")
            return
        if not is_admin_choice and group["creator_id"] != ctx.user_id:
            if await _is_admin(ctx):
                is_admin_choice = True
            else:
                ctx.clear_state()
                await ctx.answer("Нет доступа к этой площадке.")
                return

        raw = ctx.text.strip()
        if raw.casefold() in {"0", "-", "нет", "очистить"}:
            group_ids: list[int] = []
        else:
            if "token=" in raw.lower() or "vk1." in raw:
                await ctx.answer("В этом поле нужны только peer_id VK-бесед. Ключи для бесед не используются.")
                return
            refs = _split_group_refs(raw)
            if not refs:
                await ctx.answer("Отправьте хотя бы одну беседу или 0 для очистки.")
                return
            group_ids = []
            seen = set()
            for ref in refs:
                need_group_id = _parse_vk_chat_peer_id(ref)
                if not need_group_id:
                    await ctx.answer(f"Это не peer_id VK-беседы: {ref}")
                    return
                try:
                    need_group_name = await ctx.api.chat_title(need_group_id)
                except Exception:
                    await ctx.answer(f"Не смог получить доступ к VK-беседе: {ref}")
                    return
                if need_group_id in seen:
                    continue
                seen.add(need_group_id)
                group_ids.append(need_group_id)
                async with VkGroups() as vk_groups:
                    await vk_groups.upsert(
                        need_group_id,
                        title=need_group_name,
                        screen_name="",
                        target_type="chat",
                    )

        async with PartnerGroups() as partner_groups:
            await partner_groups.replace_group_need_groups(int(group["group_id"]), group_ids)
        ctx.clear_state()
        ctx.update_data(is_admin_choice=is_admin_choice)
        await ctx.answer(f"Группы подписки обновлены: {len(group_ids)}.")
        await _show_partner_group(ctx, group_db_id, is_admin_choice)

    @app.command("partner_group_rates")
    async def partner_group_rates(ctx: Ctx) -> None:
        group_db_id = int(ctx.payload["group_id"])
        is_admin_choice = bool(ctx.data.get("is_admin_choice"))
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
        if not group:
            await ctx.answer("Площадка не найдена.")
            return
        if not is_admin_choice and group["creator_id"] != ctx.user_id:
            if await _is_admin(ctx):
                is_admin_choice = True
            else:
                await ctx.answer("Нет доступа к этой площадке.")
                return
        await _show_partner_group_rates(ctx, group_db_id, is_admin_choice)

    @app.command_prefix("partner_group_rate_type.")
    async def partner_group_rate_type(ctx: Ctx) -> None:
        group_db_id = int(ctx.payload["group_id"])
        rate_type = ctx.cmd.split(".")[-1]
        is_admin_choice = bool(ctx.data.get("is_admin_choice"))
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
            if not group:
                await ctx.answer("Площадка не найдена.")
                return
            if not is_admin_choice and group["creator_id"] != ctx.user_id:
                if await _is_admin(ctx):
                    is_admin_choice = True
                else:
                    await ctx.answer("Нет доступа к этой площадке.")
                    return
            await partner_groups.change_sub_rate_type(int(group["group_id"]), rate_type)
        ctx.update_data(is_admin_choice=is_admin_choice)
        await ctx.answer("Режим тарифа обновлен.")
        await _show_partner_group_rates(ctx, group_db_id, is_admin_choice)

    @app.command("partner_group_edit_rates")
    async def partner_group_edit_rates(ctx: Ctx) -> None:
        group_db_id = int(ctx.payload["group_id"])
        rate_type = str(ctx.payload["rate_type"])
        is_admin_choice = bool(ctx.data.get("is_admin_choice"))
        if rate_type not in ("time", "msg"):
            await ctx.answer("Неверный тип тарифа.")
            return
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
        if not group:
            await ctx.answer("Площадка не найдена.")
            return
        if not is_admin_choice and group["creator_id"] != ctx.user_id:
            if await _is_admin(ctx):
                is_admin_choice = True
            else:
                await ctx.answer("Нет доступа к этой площадке.")
                return
        ctx.update_data(edit_rates_partner_db_id=group_db_id, edit_rates_type=rate_type, edit_rates_is_admin_choice=is_admin_choice)
        ctx.set_state("partner_group_rates")
        if rate_type == "time":
            await ctx.answer("Отправьте тарифы по времени, каждая строка: дней цена.\nПример:\n7 200\n30 500")
        else:
            await ctx.answer("Отправьте тарифы по сообщениям, каждая строка: сообщений цена.\nПример:\n3 200\n10 500")

    @app.state_handler("partner_group_rates")
    async def partner_group_rates_state(ctx: Ctx) -> None:
        group_db_id = int(ctx.data.get("edit_rates_partner_db_id") or 0)
        rate_type = str(ctx.data.get("edit_rates_type") or "")
        is_admin_choice = bool(ctx.data.get("edit_rates_is_admin_choice"))
        rates = _parse_access_rates(ctx.text, rate_type)
        if not group_db_id or rate_type not in ("time", "msg") or not rates:
            await ctx.answer("Неверный формат. Каждая строка должна быть: число цена.")
            return
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
            if not group:
                ctx.clear_state()
                await ctx.answer("Площадка не найдена.")
                return
            if not is_admin_choice and group["creator_id"] != ctx.user_id:
                if await _is_admin(ctx):
                    is_admin_choice = True
                else:
                    ctx.clear_state()
                    await ctx.answer("Нет доступа к этой площадке.")
                    return
            await partner_groups.replace_sub_rates(int(group["group_id"]), rate_type, rates)
        ctx.clear_state()
        ctx.update_data(is_admin_choice=is_admin_choice)
        await ctx.answer("Тарифы обновлены.")
        await _show_partner_group_rates(ctx, group_db_id, is_admin_choice)

    @app.command_prefix("partner_group_act.")
    async def partner_group_action(ctx: Ctx) -> None:
        group_db_id = int(ctx.payload["group_id"])
        is_admin_choice = bool(ctx.data.get("is_admin_choice"))
        action = ctx.cmd.split(".")[-1]
        if is_admin_choice and not await _admin_required(ctx):
            return
        async with PartnerGroups() as partner_groups:
            current_group = await partner_groups.get_by_db_id(group_db_id)
            if not current_group:
                await ctx.answer("Площадка не найдена.")
                return
            if not is_admin_choice and current_group["creator_id"] != ctx.user_id:
                await ctx.answer("Нет доступа к этой площадке.")
                return
            if action == "delete":
                await partner_groups.delete(group_db_id)
                await ctx.answer("Площадка удалена.", keyboard=kb.back("manage_partner_groups_admin" if is_admin_choice else "manage_partner_groups"))
                return
            status_map = {
                "freeze": PartnerTypes.FROZEN,
                "promotion": PartnerTypes.PROMOTION,
                "sub_groups": PartnerTypes.SUB_GROUPS,
                "promotion_and_sub": PartnerTypes.PROMOTION_AND_SUB,
            }
            await partner_groups.change_status(status_map[action], db_group_id=group_db_id)
        await ctx.answer("Статус изменен.")
        await _show_partner_group(ctx, group_db_id, is_admin_choice)

    @app.command("partner_group_schedule")
    async def partner_group_schedule(ctx: Ctx) -> None:
        group_db_id = int(ctx.payload["group_id"])
        async with PartnerGroups() as partner_groups:
            group = await partner_groups.get_by_db_id(group_db_id)
        if not group:
            await ctx.answer("Площадка не найдена.")
            return
        if not bool(ctx.data.get("is_admin_choice")) and group["creator_id"] != ctx.user_id:
            await ctx.answer("Нет доступа к этой площадке.")
            return
        async with Queue() as queue:
            events = await queue.get_group_events(group["group_id"])
        await ctx.answer(texts.group_schedule_text(events), keyboard=kb.back("manage_partner_groups_admin" if bool(ctx.data.get("is_admin_choice")) else "manage_partner_groups"))

    @app.command("manage_all_ads")
    async def manage_all_ads(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(texts.ADMIN_AD_MANAGE_TEXT, keyboard=kb.admin_ad_manage())

    @app.command("manage_ad_posts")
    async def manage_ad_posts(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        async with Posters() as posters:
            all_posters = await posters.get_all()
        if not all_posters:
            await ctx.answer("Объявлений нет.", keyboard=kb.back("manage_all_ads"))
            return
        items = [{"id": poster["id"], "title": f"#{poster['id']} статус {poster['status']}"} for poster in all_posters]
        await ctx.answer("Объявления:", keyboard=kb.list_select_kb(items, "open_poster", "manage_all_ads"))

    @app.command("open_poster")
    async def open_poster(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_poster(ctx, int(ctx.payload["item_id"]))

    @app.command_prefix("poster_act.")
    async def poster_action(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        poster_id = int(ctx.payload["poster_id"])
        action = ctx.cmd.split(".")[-1]
        async with Posters() as posters:
            if action == "delete":
                await posters.delete(poster_id)
                await ctx.answer("Объявление удалено.", keyboard=kb.back("manage_ad_posts"))
                return
            status = PosterStatus.ACTIVE if action == "activate" else PosterStatus.FROZEN
            await posters.change_status(poster_id, status)
            poster = await posters.get_by_id(poster_id)
        if action == "activate":
            suitable = (await get_suitable_groups(poster))["suitable_groups"]
            async with PartnerGroups() as partner_groups:
                for group in suitable:
                    await partner_groups.add_posters(group["group_id"], poster_id)
        await ctx.answer("Статус объявления изменен.")
        await _show_poster(ctx, poster_id)

    @app.command("poster_change_button")
    async def poster_change_button(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        poster_id = int(ctx.payload["poster_id"])
        ctx.update_data(change_button_poster_id=poster_id)
        await ctx.answer("Введите новый текст реферальной кнопки.")
        ctx.set_state("poster_button_name")

    @app.state_handler("poster_button_name")
    async def poster_button_name(ctx: Ctx) -> None:
        button_name = ctx.text.strip()
        if not button_name:
            await ctx.answer("Текст кнопки не должен быть пустым.")
            return
        poster_id = int(ctx.data["change_button_poster_id"])
        async with Posters() as posters:
            await posters.change_button_name(poster_id, button_name[:80])
        ctx.clear_state()
        await ctx.answer("Текст кнопки обновлен.")
        await _show_poster(ctx, poster_id)

    @app.command("poster_schedule_send")
    async def poster_schedule_send(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_poster_schedule_groups(ctx, int(ctx.payload["poster_id"]))

    @app.command("poster_schedule_group")
    async def poster_schedule_group(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _select_poster_schedule_group(ctx, str(ctx.payload.get("num") or ""))

    @app.state_handler("poster_schedule_group")
    async def poster_schedule_group_state(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _select_poster_schedule_group(ctx, ctx.text)

    @app.state_handler("poster_schedule_time")
    async def poster_schedule_time(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        raw_time = ctx.text.replace(".", ":")
        parts = raw_time.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            await ctx.answer("Неверный формат времени. Пример: 18:00.")
            return
        hour, minute = map(int, parts)
        if hour > 23 or minute > 59:
            await ctx.answer("Неверное время. Пример: 18:00.")
            return

        now = get_msk_now()
        activate_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if activate_time < now:
            await ctx.answer("Это время сегодня уже прошло. Введите будущее время по МСК.")
            return

        poster_id = int(ctx.data["schedule_poster_id"])
        group_id = int(ctx.data["schedule_group_id"])
        async with Queue() as queue:
            await queue.add(activate_time, group_id, poster_id)
        ctx.clear_state()
        await ctx.answer(f"Пост запланирован на {activate_time.strftime('%H:%M')} МСК.")
        await _show_poster(ctx, poster_id)

    @app.command("poster_select_groups")
    async def poster_select_groups(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        poster_id = int(ctx.payload["poster_id"])
        async with Posters() as posters:
            poster = await posters.get_by_id(poster_id)
        groups_data = await get_suitable_groups(poster)
        all_groups = []
        seen = set()
        for group in groups_data["suitable_groups"] + groups_data["selected_groups"]:
            if group["group_id"] not in seen:
                seen.add(group["group_id"])
                all_groups.append(group)
        if not all_groups:
            await ctx.answer("Нет подходящих площадок.")
            return
        values = [group["group_id"] for group in all_groups]
        selected_group_ids = {group["group_id"] for group in groups_data["selected_groups"]}
        selected_ids = {str(index + 1) for index, group in enumerate(all_groups) if group["group_id"] in selected_group_ids}
        text = ["Выберите площадки:"]
        for index, group in enumerate(all_groups, 1):
            text.append(f"{index}) {await ctx.api.group_title(group['group_id'])}")
        ctx.update_data(select_group_values=values, selected=selected_ids, select_confirm_cmd="confirm_poster_groups", select_back_cmd="open_poster", poster_id=poster_id)
        await ctx.answer("\n".join(text), keyboard=kb.number_select_kb(len(values), selected_ids, "confirm_poster_groups", "manage_ad_posts"))

    @app.command("confirm_poster_groups")
    async def confirm_poster_groups(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        poster_id = int(ctx.data["poster_id"])
        selected = _selected(ctx.data)
        values = ctx.data["select_group_values"]
        selected_group_ids = [values[int(num) - 1] for num in sorted(selected, key=int)]
        async with PartnerGroups() as partner_groups:
            await partner_groups.replace_poster_groups(poster_id, selected_group_ids)
        await ctx.answer("Площадки обновлены.")
        ctx.clear_state()
        await _show_poster(ctx, poster_id)

    @app.command("manage_ad_groups")
    async def manage_ad_groups(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        async with AdGroups() as ad_groups:
            groups = await ad_groups.get_all()
        if not groups:
            await ctx.answer("Рекламных групп нет.", keyboard=kb.back("manage_all_ads"))
            return
        items = [{"id": group["id"], "title": f"#{group['id']} {await ctx.api.group_title(group['group_id'])}"} for group in groups]
        await ctx.answer("Рекламные группы:", keyboard=kb.list_select_kb(items, "open_ad_group", "manage_all_ads"))

    @app.command("open_ad_group")
    async def open_ad_group(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_ad_group(ctx, int(ctx.payload["item_id"]))

    @app.command_prefix("ad_group_act.")
    async def ad_group_action(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        ad_group_id = int(ctx.payload["ad_group_id"])
        action = ctx.cmd.split(".")[-1]
        async with AdGroups() as ad_groups:
            if action == "delete":
                await ad_groups.delete(ad_group_id)
                await ctx.answer("Рекламная группа удалена.", keyboard=kb.back("manage_ad_groups"))
                return
            await ad_groups.change_status(ad_group_id, AdGroupsStatus.ACTIVE if action == "activate" else AdGroupsStatus.FROZEN)
        await ctx.answer("Статус изменен.")
        await _show_ad_group(ctx, ad_group_id)

    @app.command("ad_group_select_groups")
    async def ad_group_select_groups(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        ad_group_db_id = int(ctx.payload["ad_group_id"])
        async with AdGroups() as ad_groups:
            ad_group = await ad_groups.get_by_db_id(ad_group_db_id)
        async with PartnerGroups() as partner_groups:
            groups = await partner_groups.get_all(status=None)
            selected_groups = await partner_groups.get_by_ad_group_id(ad_group["group_id"])
        values = [group["group_id"] for group in groups]
        selected_group_ids = {group["group_id"] for group in selected_groups}
        selected_ids = {str(index + 1) for index, group in enumerate(groups) if group["group_id"] in selected_group_ids}
        text = ["Выберите партнерские площадки:"]
        for index, group in enumerate(groups, 1):
            text.append(f"{index}) {await ctx.api.group_title(group['group_id'])}")
        ctx.update_data(select_group_values=values, selected=selected_ids, select_confirm_cmd="confirm_ad_group_groups", select_back_cmd="open_ad_group", ad_group_db_id=ad_group_db_id, ad_group_vk_id=ad_group["group_id"])
        await ctx.answer("\n".join(text), keyboard=kb.number_select_kb(len(values), selected_ids, "confirm_ad_group_groups", "manage_ad_groups"))

    @app.command("confirm_ad_group_groups")
    async def confirm_ad_group_groups(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        ad_group_db_id = int(ctx.data["ad_group_db_id"])
        values = ctx.data["select_group_values"]
        selected = _selected(ctx.data)
        selected_group_ids = [values[int(num) - 1] for num in sorted(selected, key=int)]
        async with PartnerGroups() as partner_groups:
            await partner_groups.replace_need_groups(int(ctx.data["ad_group_vk_id"]), selected_group_ids)
        await ctx.answer("Площадки добавлены.")
        ctx.clear_state()
        await _show_ad_group(ctx, ad_group_db_id)

    @app.command("manage_requests")
    async def manage_requests_admin(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        async with UserRequests() as requests:
            reqs = await requests.get_requests(0, status=RequestStatus.MODERATED)
        if not reqs:
            await ctx.answer("Активных заявок на вывод нет.", keyboard=kb.back("menu_adminpanel"))
            return
        req = reqs[0]
        await ctx.answer(f"Заявка #{req['id']}\nПользователь: {req['user_id']}\nСумма: {req['amount']} ₽\nРеквизиты: {req['comment']}", keyboard=kb.request_admin_kb(req["id"]))

    @app.command_prefix("request_act.")
    async def request_action(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        request_id = int(ctx.payload["request_id"])
        status = RequestStatus.APPROVED if ctx.cmd.endswith("approve") else RequestStatus.REJECTED
        async with UserRequests() as requests:
            await requests.change_status(request_id, status)
        await ctx.answer("Заявка обработана.", keyboard=kb.admin_menu())

    @app.command("manual_payments")
    async def manual_payments(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        async with ManualPayments() as payments:
            all_payments = await payments.get_all()
        if not all_payments:
            await ctx.answer("Ручных оплат нет.", keyboard=kb.back("menu_adminpanel"))
            return
        for payment in all_payments[:10]:
            state = payment["payment_state"]
            await ctx.answer(
                f"Оплата #{payment['id']}\nПользователь: {state.get('from_user')}\nТип: {state.get('ad_type')}\nСумма: {state.get('current_pay_info', {}).get('sum')} ₽",
                keyboard=kb.manual_payment_kb(payment["id"]),
            )

    @app.command_prefix("manual_pay.")
    async def manual_pay_action(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        pay_id = int(ctx.payload["pay_id"])
        if ctx.cmd.endswith("apply"):
            async with ManualPayments() as payments:
                state = await payments.get_payment_state(pay_id)
                if state is None:
                    await ctx.answer("Оплата уже обработана или не найдена.", keyboard=kb.admin_menu())
                    return
                await payments.delete(pay_id)
            await activate_payment_state(ctx.api, state)
            await ctx.answer("Оплата подтверждена.", keyboard=kb.admin_menu())
        else:
            await reject_payment(ctx.api, pay_id)
            await ctx.answer("Оплата отклонена.", keyboard=kb.admin_menu())

    @app.command("admin_newsletter")
    async def admin_newsletter(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer("Кому отправить рассылку?", keyboard=kb.newsletter_type_kb())

    @app.command_prefix("answer_newsletter_to.")
    async def newsletter_target(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        target = ctx.cmd.split(".")[-1]
        ctx.update_data(newsletter_type=target)
        if target == "sub":
            async with Users() as users:
                subscribers = await users.get_users_by_status(UserStatus.NO_ROLE)
            if not subscribers:
                await ctx.answer("Подписчиков пока нет.", keyboard=kb.newsletter_type_kb())
                return
            items = []
            for user in subscribers[:9]:
                user_id = int(user["user_id"])
                title = await ctx.api.get_user_name(user_id)
                items.append({"id": user_id, "title": f"{title} ({user_id})"})
            await ctx.answer("Выберите подписчика:", keyboard=kb.list_select_kb(items, "select_newsletter_user", "admin_newsletter"))
            return
        await ctx.answer("Напишите сообщение для рассылки:")
        ctx.set_state("admin_newsletter_text")

    @app.command("select_newsletter_user")
    async def select_newsletter_user(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        ctx.update_data(newsletter_type="sub", newsletter_target_id=int(ctx.payload["item_id"]))
        await ctx.answer("Напишите сообщение для рассылки:")
        ctx.set_state("admin_newsletter_text")

    @app.state_handler("admin_newsletter_text")
    async def admin_newsletter_text(ctx: Ctx) -> None:
        target = ctx.data["newsletter_type"]
        if target == "partners":
            async with Partners() as partners:
                target_ids = [partner["user_id"] for partner in await partners.get_all()]
        elif target == "subs":
            async with Users() as users:
                target_ids = [user["user_id"] for user in await users.get_users_by_status(UserStatus.NO_ROLE)]
        elif target == "sub":
            target_ids = [int(ctx.data["newsletter_target_id"])]
        else:
            async with Users() as users:
                target_ids = [user["user_id"] for user in await users.get_users_by_status(UserStatus.ADVERTISER)]
        success = await send_newsletter(ctx.api, target_ids, target, ctx.user_id, ctx.text, attachment=ctx.attachment)
        await ctx.answer(texts.nl_results_text(len(target_ids), success, target), keyboard=kb.newsletter_type_kb())
        ctx.clear_state()

    @app.command("newsletter_moderation")
    async def newsletter_moderation(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        async with Newsletters() as newsletters:
            nls = await newsletters.get_all(is_sub=True, is_moderating=True)
        if not nls:
            await ctx.answer("Рассылок на модерации нет.", keyboard=kb.back("admin_newsletter"))
            return
        rows = []
        for nl in nls:
            rows.append([kb.text_button(f"#{nl['id']} от {nl['creator_id']}", "open_newsletter", item_id=nl["id"])])
        rows.append([kb.text_button("Назад", "admin_newsletter", "negative")])
        await ctx.answer("Рассылки на модерации:", keyboard=kb.keyboard(rows))

    @app.command("open_newsletter")
    async def open_newsletter(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_newsletter(ctx, int(ctx.payload["item_id"]))

    @app.command("newsletter_change_time")
    async def newsletter_change_time(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        ctx.update_data(edit_newsletter_id=int(ctx.payload["nl_id"]))
        await ctx.answer("Введите новое время публикации по МСК в формате 18:00.")
        ctx.set_state("newsletter_send_time")

    @app.state_handler("newsletter_send_time")
    async def newsletter_send_time(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        send_at = _parse_hh_mm(ctx.text)
        if send_at is None:
            await ctx.answer("Неверный формат времени. Пример: 18:00.")
            return
        nl_id = int(ctx.data["edit_newsletter_id"])
        async with Newsletters() as newsletters:
            await newsletters.update_send_time(nl_id, send_at)
        ctx.clear_state()
        await ctx.answer("Время публикации обновлено.")
        await _show_newsletter(ctx, nl_id)

    @app.command("newsletter_change_expires")
    async def newsletter_change_expires(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        ctx.update_data(edit_newsletter_id=int(ctx.payload["nl_id"]))
        await ctx.answer("Введите новую дату окончания в формате 20.05.2026.")
        ctx.set_state("newsletter_expires_at")

    @app.state_handler("newsletter_expires_at")
    async def newsletter_expires_at(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        expires_at = _parse_dd_mm_yyyy(ctx.text)
        if expires_at is None:
            await ctx.answer("Неверный формат даты. Пример: 20.05.2026.")
            return
        nl_id = int(ctx.data["edit_newsletter_id"])
        async with Newsletters() as newsletters:
            await newsletters.update_expires_date(nl_id, expires_at)
        ctx.clear_state()
        await ctx.answer("Дата окончания обновлена.")
        await _show_newsletter(ctx, nl_id)

    @app.command_prefix("newsletter_act.")
    async def newsletter_action(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        nl_id = int(ctx.payload["nl_id"])
        if ctx.cmd.endswith("apply"):
            async with Newsletters() as newsletters:
                nl = await newsletters.get_by_id(nl_id)
            send_at = (nl and nl["send_time"]) or time(15, 0)
            await moderate_newsletter(nl_id, send_at)
            await ctx.answer(f"Рассылка принята. Время публикации: {send_at.strftime('%H:%M')} МСК.", keyboard=kb.newsletter_type_kb())
        else:
            async with Newsletters() as newsletters:
                await newsletters.delete(nl_id)
            await ctx.answer("Рассылка удалена.", keyboard=kb.newsletter_type_kb())

    @app.command("statistics")
    async def statistics(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(await texts.main_statistics_text(), keyboard=kb.statistics_menu())

    @app.command("statistic.partners")
    async def partner_statistics(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_partner_statistics(ctx)

    @app.command_prefix("change_partner.")
    async def change_partner_statistics(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        cursor = int(ctx.data.get("partner_stats_cursor") or 0)
        cursor += 1 if ctx.cmd.endswith(".next") else -1
        await _show_partner_statistics(ctx, cursor)

    @app.command("statistic.subscribes")
    async def subscribes_statistics(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(await texts.subs_statistics_text(ctx.api), keyboard=kb.back("statistics"))

    @app.command("newsletter_statistics")
    async def newsletter_statistics(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer("Статистика по рассылке", keyboard=kb.newsletter_statistics_kb())

    @app.command("newsletter_statistics_admin")
    async def newsletter_statistics_admin(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_admin_newsletters_statistics(ctx)

    @app.command_prefix("change_admin_nls_page.")
    async def change_admin_newsletters_page(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        offset = int(ctx.data.get("admin_newsletters_offset") or 0)
        offset += 5 if ctx.cmd.endswith(".next") else -5
        await _show_admin_newsletters_statistics(ctx, offset)

    @app.command("newsletter_statistics_advert")
    async def newsletter_statistics_advert(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await _show_advert_newsletter_statistics(ctx)

    @app.command_prefix("change_advert_nl.")
    async def change_advert_newsletter(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        cursor = int(ctx.data.get("advert_newsletter_cursor") or 0)
        cursor += 1 if ctx.cmd.endswith(".next") else -1
        await _show_advert_newsletter_statistics(ctx, cursor)

    @app.command("delete_advert_nl")
    async def delete_advert_newsletter(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        nl_id = ctx.data.get("advert_newsletter_id")
        if not nl_id:
            await ctx.answer("Сначала откройте рассылку.", keyboard=kb.back("newsletter_statistics"))
            return
        async with Newsletters() as newsletters:
            await newsletters.delete(int(nl_id))
        await ctx.answer("Рассылка удалена.")
        await _show_advert_newsletter_statistics(ctx, int(ctx.data.get("advert_newsletter_cursor") or 0))

    @app.command("moderate_advert_nl")
    async def moderate_advert_newsletter(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        nl_id = ctx.data.get("advert_newsletter_id")
        if not nl_id:
            await ctx.answer("Сначала откройте рассылку.", keyboard=kb.back("newsletter_statistics"))
            return
        async with Newsletters() as newsletters:
            nl = await newsletters.get_by_id(int(nl_id))
        send_at = (nl and nl["send_time"]) or time(15, 0)
        await moderate_newsletter(int(nl_id), send_at)
        await ctx.answer(f"Рассылка принята. Время публикации: {send_at.strftime('%H:%M')} МСК.")
        await _show_advert_newsletter_statistics(ctx, int(ctx.data.get("advert_newsletter_cursor") or 0))

    @app.command_prefix("change_advert_nl_params.")
    async def change_advert_newsletter_params(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        nl_id = ctx.data.get("advert_newsletter_id")
        if not nl_id:
            await ctx.answer("Сначала откройте рассылку.", keyboard=kb.back("newsletter_statistics"))
            return
        ctx.update_data(edit_newsletter_id=int(nl_id))
        if ctx.cmd.endswith(".send_time"):
            await ctx.answer("Введите новое время публикации по МСК в формате 18:00.")
            ctx.set_state("newsletter_send_time")
        else:
            await ctx.answer("Введите новую дату окончания в формате 20.05.2026.")
            ctx.set_state("newsletter_expires_at")

    @app.command("subs_stat_menu")
    async def subs_stat_menu(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        async with UsersSubs() as subs:
            count = await subs.count()
        await ctx.answer(f"Подтвержденных подписочных доступов: {count}", keyboard=kb.back("statistics"))

    @app.command("settings_plains")
    async def settings_plains(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(texts.ad_rates_text(), keyboard=kb.manage_plains_kb())

    @app.command_prefix("manage_plain.")
    async def manage_plain(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        current_plain = ctx.cmd.split(".")[-1]
        ctx.update_data(current_plain=current_plain)
        await ctx.answer("Введите период и новую цену через пробел. Чтобы отключить тариф, вместо цены напишите -.\nПример: 5 4500")
        ctx.set_state("rate_change")

    @app.state_handler("rate_change")
    async def rate_change(ctx: Ctx) -> None:
        if ctx.text.lower() in {"-", "назад"}:
            ctx.clear_state()
            await ctx.answer(texts.ad_rates_text(), keyboard=kb.manage_plains_kb())
            return
        parts = ctx.text.split()
        if len(parts) != 2 or not parts[0].isdigit() or (parts[1] != "-" and not parts[1].isdigit()):
            await ctx.answer("Неверный формат. Пример: 5 4500 или 5 -")
            return
        edit_rate(ctx.data["current_plain"], parts[0], None if parts[1] == "-" else int(parts[1]))
        await ctx.answer("Тариф обновлен.\n\n" + texts.ad_rates_text(), keyboard=kb.manage_plains_kb())
        ctx.clear_state()

    @app.command("admin_var_settings")
    async def admin_var_settings(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(texts.var_settings_text(), keyboard=kb.var_settings_kb(get_settings_var_names()))

    @app.command("change_var_value")
    async def change_var_value(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        items = list(get_settings_var_names().items())
        index = int(ctx.payload["setting_index"])
        key, title = items[index]
        ctx.update_data(env_var_name=key)
        await ctx.answer(f"Введите новое значение для параметра {title}:")
        ctx.set_state("setting_value")

    @app.state_handler("setting_value")
    async def setting_value(ctx: Ctx) -> None:
        if not ctx.text.isdigit():
            await ctx.answer("Значение должно быть числом.")
            return
        set_key(str(BASE_DIR / ".env"), ctx.data["env_var_name"], ctx.text)
        environ[ctx.data["env_var_name"]] = ctx.text
        await ctx.answer("Параметр обновлен.\n\n" + texts.var_settings_text(), keyboard=kb.var_settings_kb(get_settings_var_names()))
        ctx.clear_state()

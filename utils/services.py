from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from os import getenv
from random import randint
from typing import Optional

import pytz

from database import (
    AdGroups,
    EventType,
    EventsCounter,
    ManualPayments,
    Newsletters,
    NewslettersTarget,
    PartnerGroups,
    PartnerTypes,
    Partners,
    Payments,
    PaymentTypes,
    Posters,
    Queue,
    UserStatus,
    Users,
    UsersSubs,
    VkGroups,
)
from database.ad_groups import AdGroupsStatus
from vkbot.api import VKApi
from utils import keyboards as kb
from utils.config import env_int, get_rates, write_json


logger = logging.getLogger(__name__)


def get_msk_now() -> datetime:
    return datetime.now(pytz.timezone("Europe/Moscow")).replace(tzinfo=None)


async def add_counter(event_type: EventType) -> None:
    async with EventsCounter() as counter:
        await counter.add(event_type)


async def get_all_periods_events(event_type: EventType) -> dict[str, int]:
    async with EventsCounter() as counter:
        return {
            period: await counter.get_count_by_period(event_type, period)
            for period in ["7 days", "1 month", "1 year", "all_time"]
        }


async def get_all_periods_pays() -> dict[str, int]:
    async with Payments() as payments:
        return {
            period: await payments.get_by_period(period)
            for period in ["7 days", "1 month", "1 year", "all_time"]
        }


def edit_rate(current_rate_name: str, rate_period: str, rate_price: Optional[int]) -> None:
    rates = get_rates()
    current_rate = rates[current_rate_name]["days"]
    last_rate_key = list(current_rate.keys())[-1]
    last_rate = last_rate_key[:-1] if last_rate_key.endswith("+") else last_rate_key

    if int(rate_period) > int(last_rate):
        last_rate_price = current_rate.pop(last_rate_key)
        current_rate[last_rate] = last_rate_price * int(last_rate)
        current_rate[f"{rate_period}+"] = rate_price
    elif int(rate_period) == int(last_rate) and last_rate_key.endswith("+"):
        current_rate[f"{last_rate}+"] = rate_price
    else:
        current_rate[rate_period] = rate_price

    write_json("paid_plains.json", rates)


async def get_suitable_groups(poster) -> dict[str, list]:
    async with PartnerGroups() as partner_groups:
        selected = await partner_groups.get_by_poster_id(poster["id"])
        suitable = await partner_groups.get_by_poster_info(poster)
    async with VkGroups() as vk_groups:
        selected = await _filter_wall_postable_groups(vk_groups, selected)
        suitable = await _filter_wall_postable_groups(vk_groups, suitable)
    return {"suitable_groups": suitable, "selected_groups": selected}


async def _filter_wall_postable_groups(vk_groups: VkGroups, groups: list) -> list:
    result = []
    for group in groups:
        meta = await vk_groups.get(abs(int(group["group_id"])))
        if (
            meta
            and meta["target_type"] == "community"
            and meta["can_wall_post"]
            and meta["token"]
        ):
            result.append(group)
    return result


async def write_poster_content(advertiser_id: int, period: int, region_codes: list[int], poster_info: dict) -> None:
    end_date = get_msk_now().date() + timedelta(days=period)
    async with Posters() as posters:
        await posters.add(
            advertiser_id,
            int(poster_info["ad_topic_id"]),
            region_codes,
            poster_info["msg"],
            poster_info.get("attachment") or "",
            "none",
            end_date,
        )


async def write_ad_group_content(advertiser_id: int, period: int, group_id: int) -> None:
    end_date = get_msk_now().date() + timedelta(days=period)
    async with AdGroups() as ad_groups:
        await ad_groups.add(advertiser_id, group_id, end_date)


async def send_log(api: VKApi, text: str, keyboard: Optional[str] = None, attachment: Optional[str] = None) -> None:
    log_peer = getenv("LOG_PEER_ID")
    peers = []
    if log_peer and log_peer.lstrip("-").isdigit():
        peers.append(int(log_peer))
    else:
        from utils.config import get_admins

        peers.extend(get_admins())

    for peer_id in peers:
        try:
            await api.send_message(peer_id, text, keyboard=keyboard, attachment=attachment)
        except Exception:
            logger.exception("Failed to send admin log to %s", peer_id)


async def send_poster_to_group(api: VKApi, group_id: int, poster, text: Optional[str] = None) -> bool:
    text = text if text is not None else poster["text"]
    attachment = poster["file_id"]
    button_name = poster["referral_button_name"] or "Купить рекламу"
    ref_link = f"https://vk.com/write-{api.group_id}?ref={group_id}" if api.group_id else ""
    wall_text = text if not ref_link else f"{text}\n\n{button_name}: {ref_link}"

    if group_id > 2_000_000_000:
        try:
            await api.send_message(group_id, wall_text, attachment=attachment)
            return True
        except Exception:
            logger.exception("Failed to send poster to VK chat %s", group_id)
            return False

    async with VkGroups() as vk_groups:
        meta = await vk_groups.get(abs(int(group_id)))
    token = meta and meta["token"]
    if not token:
        logger.warning("No VK token for community %s; wall post %s skipped", group_id, poster["id"])
        return False

    if not isinstance(api, VKApi) and hasattr(api, "wall_post"):
        try:
            await api.wall_post(group_id, wall_text, attachments=attachment)
            return True
        except Exception:
            logger.exception("Failed to post poster %s to fake/test VK wall community=%s", poster["id"], group_id)
            return False

    group_api = VKApi(token, group_id=abs(int(group_id)), api_version=api.api_version)
    try:
        post_id = await group_api.wall_post(group_id, wall_text, attachments=attachment)
        logger.info("Posted poster %s to VK wall community=%s post_id=%s", poster["id"], group_id, post_id)
        return True
    except Exception:
        logger.exception("Failed to post poster %s to VK wall community=%s", poster["id"], group_id)
        return False
    finally:
        await group_api.close()


async def send_poster_to_user(api: VKApi, user_id: int, poster, text: Optional[str] = None, keyboard: Optional[str] = None) -> bool:
    try:
        await api.send_message(user_id, text or poster["text"], keyboard=keyboard, attachment=poster["file_id"])
        return True
    except Exception:
        logger.exception("Failed to send poster to user %s", user_id)
        return False


async def send_newsletter(
    api: VKApi,
    target_ids: list[int],
    target: str,
    creator_id: int,
    text: str,
    attachment: Optional[str] = None,
    save_to_db: bool = True,
) -> int:
    if save_to_db:
        async with Newsletters() as newsletters:
            await newsletters.add(creator_id, text, NewslettersTarget[target.upper()])

    success = 0
    for peer_id in target_ids:
        try:
            await api.send_message(peer_id, text, attachment=attachment)
            success += 1
        except Exception:
            logger.exception("Failed to send newsletter to peer %s", peer_id)
    return success


async def activate_payment_state(api: VKApi, payment_state: dict) -> None:
    from_user = int(payment_state["from_user"])
    price = int(payment_state["current_pay_info"]["sum"])
    period = int(payment_state.get("sub_period") or 0)
    ad_type = payment_state["ad_type"]

    async with Users() as users:
        if not await users.in_db(from_user):
            await users.add(from_user)
        if ad_type in {"poster", "group", "newsletter"}:
            await users.update_status(from_user, UserStatus.ADVERTISER)
        db_user = await users.get_user(from_user)

    match ad_type:
        case "poster":
            await write_poster_content(
                from_user,
                period,
                list(map(int, payment_state["region_codes"])),
                payment_state["poster_info"],
            )
            pay_type = PaymentTypes.POSTER
            await api.send_message(from_user, "Оплата подтверждена. Объявление отправлено на модерацию.", keyboard=kb.advertiser_menu())
            await send_log(api, "В бот добавлено новое рекламное объявление.")

        case "group":
            ad_group_id = int(payment_state["ad_group_id"])
            selected_group_ids = list(map(int, payment_state.get("selected_group_ids") or []))
            await write_ad_group_content(from_user, period, ad_group_id)
            async with VkGroups() as vk_groups:
                await vk_groups.upsert(
                    ad_group_id,
                    title=payment_state.get("ad_group_title"),
                    screen_name=payment_state.get("ad_group_screen_name"),
                    target_type="chat",
                )
            if selected_group_ids:
                async with PartnerGroups() as partner_groups:
                    for partner_group_id in selected_group_ids:
                        await partner_groups.add_need_groups(partner_group_id, ad_group_id)
            pay_type = PaymentTypes.AD_GROUP
            await api.send_message(from_user, "Оплата подтверждена. Беседа добавлена в подписочные условия выбранных площадок.", keyboard=kb.advertiser_menu())

        case "newsletter":
            target = NewslettersTarget[payment_state["newsletter_target"].upper()]
            attachment = payment_state.get("newsletter_attachment")
            file_format = attachment[:5] if attachment else None
            async with Newsletters() as newsletters:
                await newsletters.add(
                    from_user,
                    payment_state["newsletter_text"],
                    target,
                    get_msk_now().date() + timedelta(days=period),
                    file_id=attachment,
                    file_format=file_format,
                )
            pay_type = PaymentTypes.NEWSLETTER
            await api.send_message(from_user, "Оплата подтверждена. Рассылка отправлена на модерацию.", keyboard=kb.advertiser_menu())
            await send_log(api, "В бот добавлена новая рассылка от рекламодателя.")

        case "sub_access":
            group_id = int(payment_state["access_group_id"])
            rate_type = str(payment_state["access_rate_type"])
            rate = payment_state["access_rate"]
            async with UsersSubs() as subs:
                if rate_type == "msg":
                    await subs.add_msg_sub(from_user, group_id, int(rate["msg"]))
                    access_text = f"{int(rate['msg'])} сообщений"
                elif rate_type == "time":
                    existing = await subs.get_sub(from_user, group_id)
                    base_time = get_msk_now()
                    if existing and str(existing["type"]).strip() == "time" and existing["expires_at"] and existing["expires_at"] > base_time:
                        base_time = existing["expires_at"]
                    expires_at = base_time + timedelta(days=int(rate["days"]))
                    await subs.upsert_time_sub(from_user, group_id, expires_at)
                    access_text = f"до {expires_at.strftime('%d.%m.%Y %H:%M')} МСК"
                else:
                    raise ValueError(f"Unknown sub access rate_type: {rate_type}")
            pay_type = PaymentTypes.SUB_ACCESS
            await api.send_message(from_user, f"Оплата подтверждена. Доступ к площадке club{group_id}: {access_text}.")
            await send_log(api, f"Оплачен доступ к VK-площадке club{group_id}: {access_text}.")

        case _:
            raise ValueError(f"Unknown ad_type: {ad_type}")

    async with Payments() as payments:
        await payments.add(from_user, 0, price, pay_type)

    if db_user and db_user["referral_user_id"]:
        async with Partners() as partners:
            referral_percent = env_int("REFERRAL_PERCENT", 10)
            await partners.update_balance(db_user["referral_user_id"], int(price * referral_percent / 100))


async def reject_payment(api: VKApi, pay_id: int, reason: str = "Платеж отклонен администратором.") -> None:
    async with ManualPayments() as manual:
        state = await manual.get_payment_state(pay_id)
        await manual.delete(pay_id)
    if state and state.get("from_user"):
        await api.send_message(int(state["from_user"]), reason, keyboard=kb.advertiser_menu())


async def check_for_nl_events(api: VKApi) -> None:
    async with Newsletters() as newsletters:
        nls = await newsletters.get_current_sub_nls()

    for nl in nls:
        match nl["target"]:
            case NewslettersTarget.PARTNERS:
                async with Partners() as partners:
                    target_ids = [partner["user_id"] for partner in await partners.get_all()]
            case NewslettersTarget.SUBS:
                async with Users() as users:
                    target_ids = [user["user_id"] for user in await users.get_users_by_status(UserStatus.NO_ROLE)]
            case NewslettersTarget.PARTNERS_AND_SUBS:
                async with Users() as users:
                    target_ids = [user["user_id"] for user in await users.get_users_by_status(UserStatus.NO_ROLE)]
                async with Partners() as partners:
                    target_ids += [partner["user_id"] for partner in await partners.get_all()]
            case _:
                target_ids = []

        success = await send_newsletter(
            api,
            target_ids,
            NewslettersTarget(nl["target"]).name.lower(),
            nl["creator_id"],
            nl["text"],
            attachment=nl["file_id"],
            save_to_db=False,
        )
        await send_log(api, f"Рассылка отправлена: {success}/{len(target_ids)}")


async def check_for_poster_events(api: VKApi) -> None:
    async with Queue() as queue:
        events = await queue.get_events()
    if not events:
        return

    handled_event_ids = []
    async with Posters() as posters:
        for event in events:
            poster = await posters.get_by_id(event["poster_id"])
            if not poster:
                handled_event_ids.append(event["id"])
                continue
            if await send_poster_to_group(api, int(event["group_id"]), poster):
                handled_event_ids.append(event["id"])

    if handled_event_ids:
        async with Queue() as queue:
            await queue.delete(handled_event_ids)


async def check_for_all_events(api: VKApi) -> None:
    await check_for_nl_events(api)
    await check_for_poster_events(api)


async def delete_expired_purchases() -> None:
    async with Posters() as posters:
        await posters.delete_expired()
    async with AdGroups() as ad_groups:
        await ad_groups.delete_expired()
    async with Newsletters() as newsletters:
        await newsletters.delete_expired()


async def add_day_posters_events() -> None:
    ad_limit = env_int("DAY_GROUP_AD_LIMIT", 2)
    start_time = get_msk_now().replace(hour=10, minute=0, second=0, microsecond=0)

    async with PartnerGroups() as partner_groups:
        groups = await partner_groups.get_all(status=PartnerTypes.PROMOTION)

    async with Queue() as queue:
        for group in groups:
            poster_ids = list(group["show_ad_ids"] or [])
            posters = Posters()
            posters.pool = queue.pool
            poster_ids = await posters.find_active_poster_ids(poster_ids)
            while len(poster_ids) > ad_limit:
                poster_ids.pop(randint(0, len(poster_ids) - 1))
            if not poster_ids:
                continue
            time_between_ad = 12 / len(poster_ids)
            for index, poster_id in enumerate(poster_ids):
                await queue.add(start_time + timedelta(minutes=time_between_ad * index * 60), group["group_id"], poster_id)


async def moderate_newsletter(nl_id: int, send_at: time = time(15, 0)) -> None:
    async with Newsletters() as newsletters:
        await newsletters.update_send_time(nl_id, send_at)
        await newsletters.moderate(nl_id)

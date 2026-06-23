from __future__ import annotations

import logging
from datetime import time
from os import getenv
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
    UserStatus,
    VkGroups,
)
from utils import keyboards as kb
from utils import texts
from utils.config import BASE_DIR, get_ad_categories, get_admins, get_regions, get_settings_var_names
from utils.services import (
    activate_payment_state,
    add_counter,
    edit_rate,
    get_suitable_groups,
    moderate_newsletter,
    reject_payment,
    send_log,
    send_newsletter,
)
from vkbot.app import Ctx, VKBotApp


logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in get_admins()


async def _admin_required(ctx: Ctx) -> bool:
    if _is_admin(ctx.user_id):
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
        case "newsletter":
            state["newsletter_text"] = data["newsletter_text"]
            state["newsletter_attachment"] = data.get("newsletter_attachment")
            state["newsletter_target"] = data["newsletter_target"]
    return state


def _extract_group_and_token(text: str) -> tuple[str, str | None]:
    if "token=" not in text:
        return text.strip(), None
    group_ref, token = text.split("token=", 1)
    return group_ref.strip(), token.strip().split()[0]


async def _ensure_user(ctx: Ctx) -> None:
    is_admin = _is_admin(ctx.user_id)
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


def register_handlers(app: VKBotApp) -> None:
    @app.default
    async def default(ctx: Ctx) -> None:
        await start(ctx)

    @app.command("start")
    @app.command("main_menu")
    async def start(ctx: Ctx) -> None:
        ctx.clear_state()
        await _ensure_user(ctx)
        await ctx.answer(texts.WELCOME_TEXT, keyboard=kb.main_menu(_is_admin(ctx.user_id)))

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
        try:
            group = await ctx.api.resolve_group(ctx.text)
        except Exception:
            await ctx.answer("Не смог найти VK-сообщество. Проверьте ссылку/id и повторите.")
            return

        ctx.update_data(ad_group_id=group.id)
        async with PartnerGroups() as partner_groups:
            partner_group_ids = await partner_groups.get_all_ids()
        if not partner_group_ids:
            await ctx.answer("Пока нет партнерских площадок для подписочной рекламы. Обратитесь к администратору.", keyboard=kb.advertiser_menu())
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
        await ctx.answer("\n".join(text), keyboard=kb.number_select_kb(len(partner_group_ids), set(), "confirm_select_groups", "buy_ad"))
        ctx.set_state(None)

    @app.command("select_group")
    async def select_group(ctx: Ctx) -> None:
        values = ctx.data.get("select_group_values") or []
        selected = _selected(ctx.data)
        num = str(ctx.payload.get("num") or "")
        if not num or int(num) < 1 or int(num) > len(values):
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
        group_ref, token = _extract_group_and_token(ctx.text)
        try:
            group = await ctx.api.resolve_group(group_ref)
        except Exception:
            await ctx.answer("Не смог найти VK-сообщество. Проверьте ссылку/id и повторите.")
            return
        async with VkGroups() as vk_groups:
            await vk_groups.upsert(group.id, title=group.name, screen_name=group.screen_name, token=token, can_wall_post=bool(token))
        ctx.update_data(partner_group_id=group.id, partner_group_token=token)
        await ctx.answer(texts.SELECT_REGION_PARTNER_TEXT)
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
        async with PartnerGroups() as partner_groups:
            for num in selected:
                await partner_groups.add_posters(values[int(num) - 1], poster_id)
        await ctx.answer("Площадки обновлены.")
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
        values = [group["group_id"] for group in groups]
        text = ["Выберите партнерские площадки:"]
        for index, group in enumerate(groups, 1):
            text.append(f"{index}) {await ctx.api.group_title(group['group_id'])}")
        ctx.update_data(select_group_values=values, selected=set(), select_confirm_cmd="confirm_ad_group_groups", select_back_cmd="open_ad_group", ad_group_db_id=ad_group_db_id, ad_group_vk_id=ad_group["group_id"])
        await ctx.answer("\n".join(text), keyboard=kb.number_select_kb(len(values), set(), "confirm_ad_group_groups", "manage_ad_groups"))

    @app.command("confirm_ad_group_groups")
    async def confirm_ad_group_groups(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        values = ctx.data["select_group_values"]
        selected = _selected(ctx.data)
        async with PartnerGroups() as partner_groups:
            for num in selected:
                await partner_groups.add_need_groups(values[int(num) - 1], int(ctx.data["ad_group_vk_id"]))
        await ctx.answer("Площадки добавлены.")
        await _show_ad_group(ctx, int(ctx.data["ad_group_db_id"]))

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
        nl_id = int(ctx.payload["item_id"])
        async with Newsletters() as newsletters:
            nls = await newsletters.get_all(is_sub=True)
        nl = next((item for item in nls if item["id"] == nl_id), None)
        if not nl:
            await ctx.answer("Рассылка не найдена.")
            return
        await ctx.answer(
            f"Рассылка #{nl['id']}\nАвтор: {nl['creator_id']}\nДо: {nl['expires_at']}\n\n{nl['text']}",
            keyboard=kb.keyboard(
                [
                    [kb.text_button("Принять", "newsletter_act.apply", "positive", nl_id=nl_id)],
                    [kb.text_button("Отклонить", "newsletter_act.delete", "negative", nl_id=nl_id)],
                    [kb.text_button("Назад", "newsletter_moderation", "negative")],
                ]
            ),
            attachment=nl["file_id"],
        )

    @app.command_prefix("newsletter_act.")
    async def newsletter_action(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        nl_id = int(ctx.payload["nl_id"])
        if ctx.cmd.endswith("apply"):
            await moderate_newsletter(nl_id, time(15, 0))
            await ctx.answer("Рассылка принята. Время публикации: 15:00 МСК.", keyboard=kb.newsletter_type_kb())
        else:
            async with Newsletters() as newsletters:
                await newsletters.delete(nl_id)
            await ctx.answer("Рассылка удалена.", keyboard=kb.newsletter_type_kb())

    @app.command("statistics")
    async def statistics(ctx: Ctx) -> None:
        if not await _admin_required(ctx):
            return
        await ctx.answer(await texts.main_statistics_text(), keyboard=kb.back("menu_adminpanel"))

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
        await ctx.answer("Параметр обновлен.\n\n" + texts.var_settings_text(), keyboard=kb.var_settings_kb(get_settings_var_names()))
        ctx.clear_state()

from __future__ import annotations

import json
from typing import Iterable, Optional


Color = str
MAX_INLINE_ROWS = 6
MAX_INLINE_BUTTONS = 10


def _payload(cmd: str, **data) -> str:
    value = {"cmd": cmd, **data}
    return json.dumps(value, ensure_ascii=False)


def text_button(label: str, cmd: str, color: Color = "secondary", **data) -> dict:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": _payload(cmd, **data),
        },
        "color": color,
    }


def link_button(label: str, link: str) -> dict:
    return {"action": {"type": "open_link", "label": label, "link": link}}


def keyboard(rows: Iterable[Iterable[dict]], inline: bool = True) -> str:
    row_list = []
    button_count = 0
    for row in rows:
        if len(row_list) >= MAX_INLINE_ROWS or button_count >= MAX_INLINE_BUTTONS:
            break
        next_row = []
        for button in row:
            if button_count >= MAX_INLINE_BUTTONS:
                break
            next_row.append(button)
            button_count += 1
        if next_row:
            row_list.append(next_row)
    return json.dumps(
        {"one_time": False, "inline": inline, "buttons": row_list},
        ensure_ascii=False,
    )


def main_menu(is_admin: bool = False) -> str:
    rows = [
        [text_button("Я рекламодатель", "menu_advertiser", "primary")],
        [text_button("Подключение рекламы", "menu_partner", "primary")],
    ]
    if is_admin:
        rows.append([text_button("Админ панель", "menu_adminpanel", "positive")])
    return keyboard(rows)


def back(cmd: str = "main_menu") -> str:
    return keyboard([[text_button("Назад", cmd, "negative")]])


def advertiser_menu() -> str:
    return keyboard(
        [
            [text_button("Купить рекламу", "buy_ad", "primary")],
            [text_button("Реклама", "my_ads"), text_button("Тарифы", "paid_plains")],
            [text_button("Назад", "main_menu", "negative")],
        ]
    )


def choose_ad_type() -> str:
    return keyboard(
        [
            [text_button("Объявление", "buy_ad.poster", "primary")],
            [text_button("Доступ по подписке", "buy_ad.group", "primary")],
            [text_button("Рассылка", "buy_ad.newsletter", "primary")],
            [text_button("Назад", "menu_advertiser", "negative")],
        ]
    )


def newsletter_targets() -> str:
    return keyboard(
        [
            [text_button("Партнерам", "ad_newsletter_target.partners")],
            [text_button("Подписчикам", "ad_newsletter_target.subs")],
            [text_button("Партнерам и подписчикам", "ad_newsletter_target.partners_and_subs")],
            [text_button("Назад", "buy_ad", "negative")],
        ]
    )


def partner_menu() -> str:
    return keyboard(
        [
            [text_button("Добавить площадку", "add_bot_group", "primary")],
            [text_button("Профиль", "partner_profile")],
            [text_button("Назад", "main_menu", "negative")],
        ]
    )


def partner_profile_menu(is_partner: bool) -> str:
    rows = []
    if is_partner:
        rows.extend(
            [
                [text_button("Вывод средств", "money_requests", "primary")],
                [text_button("Управление площадками", "manage_partner_groups", "primary")],
            ]
        )
    rows.append([text_button("Назад", "menu_partner", "negative")])
    return keyboard(rows)


def admin_menu() -> str:
    return keyboard(
        [
            [text_button("Реклама", "manage_all_ads", "primary")],
            [text_button("Группы партнеров", "manage_partner_groups_admin"), text_button("Заявки на вывод", "manage_requests")],
            [text_button("Ручные оплаты", "manual_payments"), text_button("Рассылка", "admin_newsletter")],
            [text_button("Параметры", "admin_var_settings"), text_button("Подписки", "subs_stat_menu")],
            [text_button("Статистика", "statistics")],
            [text_button("Назад", "main_menu", "negative")],
        ]
    )


def admin_ad_manage() -> str:
    return keyboard(
        [
            [text_button("Рекламные объявления", "manage_ad_posts", "primary")],
            [text_button("Рекламные группы", "manage_ad_groups", "primary")],
            [text_button("Тарифы", "settings_plains")],
            [text_button("Назад", "menu_adminpanel", "negative")],
        ]
    )


def statistics_menu() -> str:
    return keyboard(
        [
            [text_button("Рассылки", "newsletter_statistics", "primary")],
            [text_button("Админ группы", "subs_stat_menu")],
            [text_button("Партнеры", "statistic.partners")],
            [text_button("Подписчики", "statistic.subscribes")],
            [text_button("Назад", "menu_adminpanel", "negative")],
        ]
    )


def partner_stats_kb(total: int, cursor: int) -> str:
    rows = []
    arrows = []
    if cursor > 0:
        arrows.append(text_button("<<", "change_partner.prev"))
    if cursor + 1 < total:
        arrows.append(text_button(">>", "change_partner.next"))
    if arrows:
        rows.append(arrows)
    rows.append([text_button("Назад", "statistics", "negative")])
    return keyboard(rows)


def newsletter_statistics_kb() -> str:
    return keyboard(
        [
            [text_button("Рассылка рекламодателей", "newsletter_statistics_advert", "primary")],
            [text_button("Рассылка админа", "newsletter_statistics_admin")],
            [text_button("Назад", "statistics", "negative")],
        ]
    )


def admin_newsletters_page_kb(total: int, offset: int, page_size: int = 5) -> str:
    rows = []
    arrows = []
    if offset > 0:
        arrows.append(text_button("<<", "change_admin_nls_page.prev"))
    if offset + page_size < total:
        arrows.append(text_button(">>", "change_admin_nls_page.next"))
    if arrows:
        rows.append(arrows)
    rows.append([text_button("Назад", "newsletter_statistics", "negative")])
    return keyboard(rows)


def advert_newsletter_kb(total: int, cursor: int, is_moderating: bool) -> str:
    rows = []
    if is_moderating:
        rows.append([text_button("Принять", "moderate_advert_nl", "positive")])
        rows.append([text_button("Отклонить", "delete_advert_nl", "negative")])
    else:
        rows.append([text_button("Время публикации", "change_advert_nl_params.send_time")])
        rows.append([text_button("Дата окончания", "change_advert_nl_params.expires_at")])
        rows.append([text_button("Удалить", "delete_advert_nl", "negative")])

    arrows = []
    if cursor > 0:
        arrows.append(text_button("<<", "change_advert_nl.prev"))
    if cursor + 1 < total:
        arrows.append(text_button(">>", "change_advert_nl.next"))
    if arrows:
        rows.append(arrows)
    rows.append([text_button("Назад", "newsletter_statistics", "negative")])
    return keyboard(rows)


def categories_kb(categories: dict[str, str], selected: set[str]) -> str:
    rows = []
    rows.append([text_button(("✓ " if "0" in selected else "") + "Все", "select_category", category_id="0")])
    row = []
    for category_id, category_name in list(categories.items())[:8]:
        label = ("✓ " if category_id in selected else "") + category_name[:34]
        row.append(text_button(label, "select_category", category_id=category_id))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([text_button("Продолжить", "confirm_select_ad_categories", "positive")])
    return keyboard(rows)


def number_select_kb(count: int, selected: set[str], confirm_cmd: str, back_cmd: Optional[str] = None) -> str:
    rows = []
    row = []
    max_numbers = 8 if back_cmd else 9
    for i in range(1, min(count, max_numbers) + 1):
        value = str(i)
        row.append(text_button(("✓ " if value in selected else "") + value, "select_group", num=value))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([text_button("Продолжить", confirm_cmd, "positive")])
    if back_cmd:
        rows.append([text_button("Назад", back_cmd, "negative")])
    return keyboard(rows)


def number_action_kb(count: int, cmd: str, back_cmd: Optional[str] = None) -> str:
    rows = []
    row = []
    max_numbers = 8 if back_cmd else 9
    for i in range(1, min(count, max_numbers) + 1):
        row.append(text_button(str(i), cmd, num=str(i)))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if back_cmd:
        rows.append([text_button("Назад", back_cmd, "negative")])
    return keyboard(rows)


def list_select_kb(items: list[dict], cmd: str, back_cmd: str, label_key: str = "title") -> str:
    rows = []
    row = []
    max_items = 9
    for item in items[:max_items]:
        row.append(text_button(str(item[label_key])[:32], cmd, item_id=item["id"]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([text_button("Назад", back_cmd, "negative")])
    return keyboard(rows)


def manage_partner_group_kb(group_id: int, status: int, is_admin: bool = False) -> str:
    rows = []
    if status != 0:
        rows.append([text_button("Заморозить", "partner_group_act.freeze", "negative", group_id=group_id)])
    rows.append([
        text_button("Только подписки", "partner_group_act.sub_groups", "primary", group_id=group_id),
        text_button("Только реклама", "partner_group_act.promotion", "primary", group_id=group_id),
    ])
    rows.append([text_button("Реклама и подписки", "partner_group_act.promotion_and_sub", "primary", group_id=group_id)])
    rows.append([
        text_button("Группы подписки", "partner_group_need_groups", "primary", group_id=group_id),
        text_button("Тарифы доступа", "partner_group_rates", "primary", group_id=group_id),
        text_button("Расписание", "partner_group_schedule", group_id=group_id),
    ])
    rows.append([
        text_button("Удалить", "partner_group_act.delete", "negative", group_id=group_id),
    ])
    rows.append([text_button("Назад", "manage_partner_groups_admin" if is_admin else "manage_partner_groups", "negative")])
    return keyboard(rows)


def manage_plains_kb() -> str:
    return keyboard(
        [
            [text_button("Объявления", "manage_plain.poster")],
            [text_button("Группы", "manage_plain.group")],
            [text_button("Рассылка", "manage_plain.newsletter")],
            [text_button("Назад", "manage_all_ads", "negative")],
        ]
    )


def var_settings_kb(settings: dict[str, str]) -> str:
    rows = []
    for index, title in enumerate(settings.values()):
        rows.append([text_button(title[:40], "change_var_value", setting_index=index)])
    rows.append([text_button("Назад", "menu_adminpanel", "negative")])
    return keyboard(rows)


def poster_admin_kb(poster_id: int, status: int) -> str:
    rows = []
    if status == 0:
        rows.append([text_button("Принять", "poster_act.activate", "positive", poster_id=poster_id)])
    elif status == 1:
        rows.append([text_button("Заморозить", "poster_act.freeze", "negative", poster_id=poster_id)])
    elif status == 2:
        rows.append([text_button("Активировать", "poster_act.activate", "positive", poster_id=poster_id)])
    rows.append([text_button("Выбрать площадки", "poster_select_groups", "primary", poster_id=poster_id)])
    rows.append([text_button("Текст кнопки", "poster_change_button", "primary", poster_id=poster_id)])
    rows.append([text_button("Запланировать пост", "poster_schedule_send", "primary", poster_id=poster_id)])
    rows.append([text_button("Удалить", "poster_act.delete", "negative", poster_id=poster_id)])
    rows.append([text_button("Назад", "manage_all_ads", "negative")])
    return keyboard(rows)


def ad_group_admin_kb(ad_group_id: int, status: int) -> str:
    rows = []
    if status == 1:
        rows.append([text_button("Заморозить", "ad_group_act.freeze", "negative", ad_group_id=ad_group_id)])
    else:
        rows.append([text_button("Активировать", "ad_group_act.activate", "positive", ad_group_id=ad_group_id)])
    rows.append([text_button("Добавить площадки", "ad_group_select_groups", "primary", ad_group_id=ad_group_id)])
    rows.append([text_button("Удалить", "ad_group_act.delete", "negative", ad_group_id=ad_group_id)])
    rows.append([text_button("Назад", "manage_all_ads", "negative")])
    return keyboard(rows)


def request_admin_kb(request_id: int) -> str:
    return keyboard(
        [
            [text_button("Принять", "request_act.approve", "positive", request_id=request_id)],
            [text_button("Отклонить", "request_act.reject", "negative", request_id=request_id)],
            [text_button("Назад", "menu_adminpanel", "negative")],
        ]
    )


def manual_payment_kb(pay_id: int) -> str:
    return keyboard(
        [
            [text_button("Подтвердить", "manual_pay.apply", "positive", pay_id=pay_id)],
            [text_button("Отклонить", "manual_pay.decline", "negative", pay_id=pay_id)],
        ]
    )


def newsletter_type_kb() -> str:
    return keyboard(
        [
            [text_button("Партнерам", "answer_newsletter_to.partners")],
            [text_button("Подписчикам", "answer_newsletter_to.subs")],
            [text_button("Рекламодателям", "answer_newsletter_to.advertisers")],
            [text_button("Подписчику", "answer_newsletter_to.sub")],
            [text_button("Модерация рассылок", "newsletter_moderation", "primary")],
            [text_button("Назад", "menu_adminpanel", "negative")],
        ]
    )


def open_bot_link(bot_group_id: int, ref_group_id: int, label: str = "Купить рекламу") -> str:
    link = f"https://vk.com/write-{bot_group_id}?ref={ref_group_id}"
    return keyboard([[link_button(label, link)]])


def subscription_check_kb(groups: list[tuple], main_group_id: int) -> str:
    rows = []
    for group in groups[:5]:
        group_id, name = int(group[0]), str(group[1])
        link = group[2] if len(group) > 2 else None
        if not link:
            if group_id > 2_000_000_000:
                link = f"https://vk.com/im?sel=c{group_id - 2_000_000_000}"
            else:
                link = f"https://vk.com/club{abs(group_id)}"
        rows.append([link_button(name[:40], link)])
    rows.append([text_button("Проверить подписку", "check_subs", "positive", main_group_id=main_group_id)])
    return keyboard(rows)

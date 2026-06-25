from __future__ import annotations

from datetime import time
from os import getenv
from typing import Iterable

from asyncpg import Record

from database import (
    AdGroupsStatus,
    EventType,
    NewslettersTarget,
    PartnerTypes,
    PaymentTypes,
    Payments,
    RequestStatus,
    UserRequests,
    Users,
    UserStatus,
)
from utils.config import get_ad_categories, get_regions, get_settings_var_names


WELCOME_TEXT = (
    "Привет, этот бот предназначен для управления и рассылки рекламы.\n\n"
    "С какой целью будете использовать данный бот?"
)

ADVERTISER_MENU_TEXT = (
    "Можете управлять уже купленной рекламой или купить новую.\n"
    "Рекламу можно купить 3-х видов:\n\n"
    "1) Рекламное объявление\n"
    "2) Доступ по подписке\n"
    "3) Рассылка партнерам и подписчикам бота"
)

NEW_PARTNER_MENU_TEXT = (
    "Вы можете подключить вашу VK-площадку и получать процент за рекламодателей, "
    "которые придут через вашу площадку.\n\n"
    "Деньги можно вывести через профиль."
)

ADMIN_MENU_TEXT = "Админ панель"
ADMIN_AD_MANAGE_TEXT = "В данном разделе можно управлять рекламными объявлениями, рекламными группами и тарифами."

ADD_PARTNER_GROUP = (
    "Отправьте ссылку, короткое имя или ID VK-сообщества/площадки.\n\n"
    "Для публикации рекламы на стену чужого сообщества нужен токен этого сообщества с правами wall/photos/messages. "
    "Можно отправить так:\n"
    "club123456 token=vk1.a.xxxxx\n"
    "или так:\n"
    "club123456 vk1.a.xxxxx\n\n"
    "Без token площадка будет добавлена для учета и подписочных проверок, но автопубликация на стену может быть недоступна."
)

ADD_VK_GROUP_TOKEN_TEXT = (
    "Отправьте VK-сообщество и его ключ доступа в одном сообщении.\n\n"
    "Этот режим только сохраняет ключ и не добавляет сообщество как вашу рекламную площадку.\n"
    "Формат:\n"
    "club123456 vk1.a.xxxxx\n"
    "или:\n"
    "club123456 token=vk1.a.xxxxx"
)

SELECT_REGION_AD_TEXT = (
    "Укажите коды регионов через пробел или 0, если реклама подходит для всей России.\n"
    "Пример: 77 78 или 0"
)

SELECT_REGION_PARTNER_TEXT = (
    "Укажите коды регионов площадки через пробел или 0, если площадка подходит для всей России.\n"
    "Пример: 77 78 или 0"
)

SELECT_PARTNER_AD_CATEGORY_TEXT = "Выберите тематики рекламы, которые можно показывать на этой площадке:"

BUY_POSTER_AD_TEXT = (
    "Введите рекламное объявление.\n\n"
    "Первая строка - тематика из списка ниже.\n"
    "Со второй строки - текст рекламы.\n"
    "Фото/видео можно прикрепить к этому же сообщению.\n\n"
    "Тематики:\n{}"
)

BUY_GROUP_AD_TEXT = (
    "Введите VK-сообщество, которое хотите продвигать подпиской.\n"
    "Пример: club123456, @screen_name, https://vk.com/screen_name или 123456"
)

BUY_NEWSLETTER_AD_TEXT = "Введите текст рассылки. Фото/видео можно прикрепить к этому же сообщению."

ADD_GROUP_SUCCESSFUL_TEXT = "Группа может быть добавлена. Теперь выберите партнерские площадки для подписки."
ADD_POSTER_SUCCESSFUL_TEXT = "Объявление записано. Регионы: {}.\n\nТеперь напишите количество дней покупки, например 8."
ADD_NEWSLETTER_SUCCESSFUL_TEXT = "Рассылка записана.\n\nТеперь напишите количество дней покупки, например 8."

REQUEST_PAY_DETAILS_TEXT = (
    "Введите реквизиты для вывода: номер карты, номер телефона с банком или кошелек YooMoney.\n"
    "Пример: 1234 5678 9000 0000 или +79991234567 Т-Банк"
)


def categories_text() -> str:
    return "\n".join(f"- {name}" for name in get_ad_categories().values())


def ad_rates_text() -> str:
    rates = get_rates_safe()
    names = {
        "poster": "Тарифы на объявления",
        "group": "Тарифы на доступ по подписке",
        "newsletter": "Тарифы на рассылку",
    }
    parts = ["В данном боте представлены следующие тарифы\n"]
    for rate_key, title in names.items():
        parts.append(title + ":")
        for days, price in rates.get(rate_key, {}).get("days", {}).items():
            suffix = " ₽/день" if days.endswith("+") else " ₽"
            parts.append(f"{days} дней - {price if price else 'Недоступно'}{suffix if price else ''}")
        parts.append("")
    return "\n".join(parts).strip()


def get_rates_safe() -> dict:
    from utils.config import get_rates

    return get_rates()


def calc_price(ad_type: str, period: int) -> int | None:
    rates = get_rates_safe()[ad_type]["days"]
    if str(period) in rates:
        return rates[str(period)]
    last_key = list(rates.keys())[-1]
    if last_key.endswith("+") and period >= int(last_key[:-1]):
        return int(rates[last_key]) * period
    return None


def payment_instruction(period: int, price: int) -> str:
    return (
        f"За {period} д. необходимо заплатить {price} ₽.\n\n"
        "YooMoney будет подключен последним этапом, сейчас покупка оформляется через ручное подтверждение.\n"
        "Отправьте сюда комментарий к платежу или скриншот. Админ подтвердит заявку, после чего реклама активируется."
    )


def manual_pay_request_text(pay_sum: int, pay_detail: str, user_id: int, ad_type: str) -> str:
    type_names = {
        "poster": "рекламное объявление",
        "group": "рекламная группа",
        "newsletter": "рассылка",
        "sub_access": "доступ к площадке",
    }
    return (
        "Новый запрос на оплату\n\n"
        f"Пользователь VK: {user_id}\n"
        f"Тип: {type_names.get(ad_type, ad_type)}\n"
        f"Сумма платежа: {pay_sum} ₽\n"
        f"Примечание к платежу:\n{pay_detail or 'Нет'}"
    )


def partner_group_added_text(region_codes: Iterable[str]) -> str:
    regions = get_regions()
    selected = ", ".join(regions.get(str(code), str(code)) for code in region_codes)
    return (
        "Площадка успешно добавлена.\n"
        f"Регионы: {selected}\n\n"
        "За каждого реферала, который станет рекламодателем, вы будете получать процент."
    )


def format_group_title(group: Record | dict | None) -> str:
    if not group:
        return "Недоступно"
    if group.get("title"):
        return group["title"]
    if group.get("screen_name"):
        return group["screen_name"]
    return str(group.get("group_id") or group.get("id"))


def admin_poster_text(poster: Record | dict) -> str:
    statuses = {-1: "Удалено", 0: "На модерации", 1: "Активно", 2: "Заморожено"}
    regions = get_regions()
    categories = get_ad_categories()
    selected_regions = ", ".join(regions.get(str(code), str(code)) for code in poster["region_codes"])
    file_text = "Есть" if poster["file_id"] else "Отсутствует"
    return (
        f"Объявление #{poster['id']}\n\n"
        f"Статус: {statuses.get(poster['status'], poster['status'])}\n"
        f"Действует до: {poster['end_date'].strftime('%d.%m.%Y')}\n"
        f"Регионы: {selected_regions}\n"
        f"Тема: {categories.get(str(poster['topic_id']), poster['topic_id'])}\n"
        f"Файл: {file_text}\n"
        f"Текст кнопки: {poster['referral_button_name'] or 'Купить рекламу'}\n\n"
        f"{poster['text']}"
    )


def admin_ad_group_text(ad_group: Record | dict, title: str = "Недоступно") -> str:
    statuses = {
        AdGroupsStatus.ACTIVE: "Активная",
        AdGroupsStatus.MODERATED: "На модерации",
        AdGroupsStatus.FROZEN: "Заморожена",
        AdGroupsStatus.DELETED: "Удалена",
        1: "Активная",
        0: "На модерации",
        2: "Заморожена",
        -1: "Удалена",
    }
    return (
        f"Рекламная группа #{ad_group['id']}\n\n"
        f"Название: {title}\n"
        f"VK ID: {ad_group['group_id']}\n"
        f"Рекламодатель: {ad_group['creator_id']}\n"
        f"Статус: {statuses.get(ad_group['status'], ad_group['status'])}\n"
        f"Действует до: {ad_group['end_date'].strftime('%d.%m.%Y')}"
    )


def partner_group_text(group: Record | dict, title: str = "Недоступно") -> str:
    statuses = {
        PartnerTypes.FROZEN: "Заморожено",
        PartnerTypes.SUB_GROUPS: "Доступ по подписке",
        PartnerTypes.PROMOTION: "Показ рекламы",
        PartnerTypes.PROMOTION_AND_SUB: "Показ рекламы и доступ по подписке",
        0: "Заморожено",
        1: "Доступ по подписке",
        2: "Показ рекламы",
        3: "Показ рекламы и доступ по подписке",
    }
    regions = get_regions()
    categories = get_ad_categories()
    region_text = ", ".join(regions.get(str(code), str(code)) for code in (group["region_codes"] or []))
    cats = group["poster_categories"] or []
    category_text = "все" if 0 in cats else ", ".join(categories.get(str(code), str(code)) for code in cats)
    rate_type = str(group["sub_rate_type"] or "none").strip() if "sub_rate_type" in group else "none"
    rate_text = {"none": "без оплаты", "time": "по времени", "msg": "по сообщениям"}.get(rate_type, rate_type)
    return (
        f"Площадка: {title}\n"
        f"VK ID: {group['group_id']}\n"
        f"Тип: {statuses.get(group['partner_type'], group['partner_type'])}\n"
        f"Тариф доступа: {rate_text}\n"
        f"Подключено рекламы: {len(group['show_ad_ids'] or [])}\n"
        f"Групп для подписки: {len(group['need_groups'] or [])}\n"
        f"Регионы: {region_text or '-'}\n"
        f"Категории: {category_text or '-'}"
    )


def partner_requests_text(requests: list[Record]) -> str:
    if not requests:
        return "У вас пока нет заявок на вывод средств."
    status_text = {0: "На рассмотрении", -1: "Отменена", 2: "Успешная", 3: "Отклонена"}
    rows = ["Заявки на вывод средств\n"]
    for req in requests:
        rows.append(f"#{req['id']} - {status_text.get(req['status'], req['status'])}: {req['amount']} ₽, {req['comment']}")
    return "\n".join(rows)


async def partner_profile_text(user_id: int) -> str:
    from database import Partners

    async with Partners() as partners:
        groups = await partners.get_user_groups(user_id)
    if not groups:
        return "У вас еще нет подключенных площадок."
    balance = groups[0]["balance"]
    count = len([group for group in groups if group["group_id"]])
    return f"Профиль\n\nВаш VK ID: {user_id}\nБаланс: {balance} ₽\nПодключенных площадок: {count}"


async def my_ads_text(user_id: int, group_titles: dict[int, str]) -> str:
    from database import AdGroups, Posters, PosterStatus, AdGroupsStatus

    async with Posters() as posters:
        active_posters = await posters.get_by_status(PosterStatus.ACTIVE, user_id)
        moderated_posters = await posters.get_by_status(PosterStatus.MODERATED, user_id)
    async with AdGroups() as ad_groups:
        active_groups = await ad_groups.get_by_status(AdGroupsStatus.ACTIVE, user_id)

    rows = ["Ваша активная реклама\n"]
    rows.append(f"Постов: {len(active_posters) + len(moderated_posters)}")
    rows.append(f"- Активных: {len(active_posters)}")
    rows.append(f"- На модерации: {len(moderated_posters)}\n")
    rows.append(f"Рекламных групп: {len(active_groups)}")
    for group in active_groups:
        rows.append(f"- {group_titles.get(group['group_id'], group['group_id'])}, до {group['end_date'].strftime('%d.%m.%Y')}")
    return "\n".join(rows)


def group_schedule_text(events: list[Record]) -> str:
    if not events:
        return "На сегодня расписание не создано или нет подходящих рекламных постов."
    rows = ["Расписание отправки рекламы:"]
    for event in events:
        rows.append(f"- {event['activate_time'].strftime('%H:%M')}")
    return "\n".join(rows)


def nl_results_text(total: int, success: int, target: str) -> str:
    percent = int(success * 100 / total) if total else 0
    target_to_ru = {
        "partners": "партнеры",
        "subs": "подписчики",
        "sub": "подписчик",
        "advertisers": "рекламодатели",
        "partners_and_subs": "подписчики и партнеры",
    }
    return (
        "Статистика рассылки\n\n"
        f"Цель: {target_to_ru.get(target, target)}\n"
        f"Всего пользователей: {total}\n"
        f"Удалось отправить: {success} ({percent}%)"
    )


def _short_text(value: str, limit: int = 700) -> str:
    value = value or ""
    return value if len(value) <= limit else value[: limit - 3] + "..."


async def _vk_user_label(api, user_id: int) -> str:
    try:
        name = await api.get_user_name(int(user_id))
    except Exception:
        name = "Недоступно"
    return f"{name} (VK ID: {user_id})"


def admin_nl_text(nls: list[Record]) -> str:
    if not nls:
        return "На данный момент нет рассылок от администратора."

    target_names = {
        int(NewslettersTarget.SUBS): "подписчикам",
        int(NewslettersTarget.SUB): "подписчику",
        int(NewslettersTarget.PARTNERS): "партнерам",
        int(NewslettersTarget.ADVERTISERS): "рекламодателям",
        int(NewslettersTarget.PARTNERS_AND_SUBS): "партнерам и подписчикам",
    }
    rows = ["Все рассылки от администраторов\n"]
    for nl in nls:
        created = nl["creation_time"].strftime("%d.%m.%Y %H:%M") if nl["creation_time"] else "-"
        target = target_names.get(int(nl["target"]), str(nl["target"]))
        rows.append(f"Рассылка #{nl['id']} {target} ({created})")
        rows.append(_short_text(nl["text"], 500))
        rows.append("")
    return "\n".join(rows).strip()


async def advert_nl_text(api, nls: list[Record], current_nl_id: int) -> str:
    if not nls:
        return "На данный момент нет активных рассылок от рекламодателей."

    target_names = {
        int(NewslettersTarget.SUBS): "подписчики",
        int(NewslettersTarget.PARTNERS): "партнеры",
        int(NewslettersTarget.PARTNERS_AND_SUBS): "партнеры и подписчики",
    }
    current_nl = nls[current_nl_id]
    send_at = current_nl["send_time"] or time(15, 0)
    created = current_nl["creation_time"].strftime("%d.%m.%Y %H:%M") if current_nl["creation_time"] else "-"
    expires_at = current_nl["expires_at"].strftime("%d.%m.%Y") if current_nl["expires_at"] else "-"
    status = "на модерации" if current_nl["is_moderating"] else "активна"
    author = await _vk_user_label(api, int(current_nl["creator_id"]))

    return (
        f"Рассылка от рекламодателя {current_nl_id + 1}/{len(nls)}\n\n"
        f"ID: {current_nl['id']}\n"
        f"Рекламодатель: {author}\n"
        f"Цель: {target_names.get(int(current_nl['target']), current_nl['target'])}\n"
        f"Статус: {status}\n"
        f"Дата покупки: {created}\n"
        f"Дата окончания: {expires_at}\n"
        f"Время публикации: {send_at.strftime('%H:%M')} МСК\n\n"
        f"Текст:\n{_short_text(current_nl['text'], 1200)}"
    )


async def partner_stats_text(api, partner_info: Record) -> str:
    partner_id = int(partner_info["user_id"])
    partner_name = await _vk_user_label(api, partner_id)

    async with Users() as users:
        referrals_info = await users.get_referrals(partner_id)

    rows = [f"Партнер: {partner_name}", f"Баланс: {partner_info['balance']} ₽", "", "Рефералы:"]
    if not referrals_info:
        rows.append("- Нет")
    for referral in referrals_info[:20]:
        referral_id = int(referral["user_id"])
        async with Payments() as payments:
            all_ref_payments = await payments.get_all(from_user=referral_id)
        payment_sum = sum(pay["sum"] for pay in all_ref_payments)
        referral_name = await _vk_user_label(api, referral_id)
        rows.append(f"- {referral_name}: куплено рекламы на {payment_sum} ₽")
    if len(referrals_info) > 20:
        rows.append(f"- Еще {len(referrals_info) - 20} рефералов не показаны в сообщении")

    rows.extend(["", "Успешные выводы:"])
    async with UserRequests() as requests:
        success_reqs = await requests.get_requests(partner_id, RequestStatus.APPROVED)
    if not success_reqs:
        rows.append("- Нет")
    for req in success_reqs[:20]:
        rows.append(f"- Вывод {req['amount']} ₽ на реквизиты: {_short_text(req['comment'] or '-', 120)}")
    if len(success_reqs) > 20:
        rows.append(f"- Еще {len(success_reqs) - 20} выводов не показаны в сообщении")
    return "\n".join(rows)


async def subs_statistics_text(api) -> str:
    async with Users() as users:
        subs_info = await users.get_users_by_status(UserStatus.NO_ROLE)

    rows = [f"Все подписчики: {len(subs_info)}"]
    if not subs_info:
        rows.append("- Нет")
        return "\n".join(rows)

    async with Users() as users:
        for sub_info in subs_info[:50]:
            user = await users.get_user(int(sub_info["user_id"]))
            sub_name = await _vk_user_label(api, int(sub_info["user_id"]))
            referral_text = ""
            if user and user["referral_user_id"] is not None:
                referral_name = await _vk_user_label(api, int(user["referral_user_id"]))
                referral_text = f" (пришел от партнера {referral_name})"
            rows.append(f"- {sub_name}{referral_text}")
    if len(subs_info) > 50:
        rows.append(f"- Еще {len(subs_info) - 50} подписчиков не показаны в сообщении")
    return "\n".join(rows)


async def main_statistics_text() -> str:
    from utils.services import get_all_periods_events, get_all_periods_pays

    async with Users() as users:
        subs = await users.get_users_by_status(UserStatus.NO_ROLE)
        partners = await users.get_users_by_status(UserStatus.PARTNER)
        adverts = await users.get_users_by_status(UserStatus.ADVERTISER)
        user_count = await users.get_user_count()
    events = await get_all_periods_events(EventType.SUB_BUTTON_PRESSED)
    pays = await get_all_periods_pays()
    adverts_events = await get_all_periods_events(EventType.ADDED_NEW_ADVERT)
    return (
        "Статистика по боту\n\n"
        f"Всего пользователей: {user_count}\n"
        f"- Партнеров: {len(partners)}\n"
        f"- Рекламодателей: {len(adverts)}\n"
        f"- Подписчиков: {len(subs)}\n\n"
        "Подтвердили подписку:\n"
        f"- 7 дней: {events['7 days']}\n"
        f"- Месяц: {events['1 month']}\n"
        f"- Год: {events['1 year']}\n"
        f"- Все время: {events['all_time']}\n\n"
        "Прибыль проекта:\n"
        f"- 7 дней: {pays['7 days'] or 0} ₽\n"
        f"- Месяц: {pays['1 month'] or 0} ₽\n"
        f"- Год: {pays['1 year'] or 0} ₽\n"
        f"- Все время: {pays['all_time'] or 0} ₽\n\n"
        "Пришло рекламодателей:\n"
        f"- 7 дней: {adverts_events['7 days']}\n"
        f"- Месяц: {adverts_events['1 month']}\n"
        f"- Год: {adverts_events['1 year']}\n"
        f"- Все время: {adverts_events['all_time']}"
    )


def var_settings_text() -> str:
    rows = ["Параметры бота\n"]
    for key, title in get_settings_var_names().items():
        rows.append(f"{title}: {getenv(key, '')}")
    return "\n".join(rows)

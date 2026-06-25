from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from database.vk_meta import VkProcessedMessages
from vkbot.api import VKApi, attachment_to_vk_id, parse_payload


logger = logging.getLogger(__name__)


@dataclass
class StateStore:
    states: dict[int, str] = field(default_factory=dict)
    data: dict[int, dict[str, Any]] = field(default_factory=dict)

    def get_state(self, user_id: int) -> Optional[str]:
        return self.states.get(user_id)

    def set_state(self, user_id: int, state: Optional[str]) -> None:
        if state is None:
            self.states.pop(user_id, None)
        else:
            self.states[user_id] = state

    def clear(self, user_id: int) -> None:
        self.states.pop(user_id, None)
        self.data.pop(user_id, None)

    def update(self, user_id: int, **kwargs) -> None:
        self.data.setdefault(user_id, {}).update(kwargs)

    def get_data(self, user_id: int) -> dict[str, Any]:
        return self.data.setdefault(user_id, {})


@dataclass
class Ctx:
    api: VKApi
    state: StateStore
    update: dict[str, Any]
    message: dict[str, Any]

    @property
    def user_id(self) -> int:
        return int(self.message["from_id"])

    @property
    def peer_id(self) -> int:
        return int(self.message["peer_id"])

    @property
    def text(self) -> str:
        return (self.message.get("text") or "").strip()

    @property
    def payload(self) -> dict[str, Any]:
        return parse_payload(self.message.get("payload"))

    @property
    def cmd(self) -> str:
        return str(self.payload.get("cmd") or "")

    @property
    def attachment(self) -> Optional[str]:
        return attachment_to_vk_id(self.message.get("attachments") or [])

    @property
    def ref(self) -> Optional[str]:
        return self.message.get("ref") or self.message.get("ref_source")

    async def answer(self, text: str, keyboard: Optional[str] = None, attachment: Optional[str] = None) -> int:
        return await self.api.send_message(self.peer_id, text, keyboard=keyboard, attachment=attachment)

    def set_state(self, state: Optional[str]) -> None:
        self.state.set_state(self.user_id, state)

    def clear_state(self) -> None:
        self.state.clear(self.user_id)

    def update_data(self, **kwargs) -> None:
        self.state.update(self.user_id, **kwargs)

    @property
    def data(self) -> dict[str, Any]:
        return self.state.get_data(self.user_id)


Handler = Callable[[Ctx], Any]
WallPostHandler = Callable[[VKApi, dict[str, Any]], Any]


TEXT_COMMAND_ALIASES = {
    "я рекламодатель": "menu_advertiser",
    "подключение рекламы": "menu_partner",
    "админ панель": "menu_adminpanel",
    "админ-панель": "menu_adminpanel",
    "купить рекламу": "buy_ad",
    "тарифы": "paid_plains",
    "объявление": "buy_ad.poster",
    "доступ по подписке": "buy_ad.group",
    "рассылка": "buy_ad.newsletter",
    "добавить площадку": "add_bot_group",
    "сохранить ключ": "add_vk_group_token",
    "добавить ключ": "add_vk_group_token",
    "профиль": "partner_profile",
    "вывод средств": "money_requests",
    "управление площадками": "manage_partner_groups",
    "группы партнеров": "manage_partner_groups_admin",
    "группы партнёров": "manage_partner_groups_admin",
    "заявки на вывод": "manage_requests",
    "ручные оплаты": "manual_payments",
    "параметры": "admin_var_settings",
    "подписки": "subs_stat_menu",
    "статистика": "statistics",
    "назад": "main_menu",
}


def normalize_text_command(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


async def is_new_message(api: VKApi, message: dict[str, Any]) -> bool:
    conversation_message_id = int(message.get("conversation_message_id") or message.get("id") or 0)
    if conversation_message_id <= 0:
        return True
    peer_id = int(message["peer_id"])
    group_id = int(api.group_id or 0)
    vk_message_id = int(message.get("id") or 0) or None
    async with VkProcessedMessages() as processed:
        return await processed.mark(group_id, peer_id, conversation_message_id, vk_message_id)


class VKBotApp:
    def __init__(self, api: VKApi):
        self.api = api
        self.state = StateStore()
        self.command_handlers: dict[str, Handler] = {}
        self.prefix_handlers: list[tuple[str, Handler]] = []
        self.state_handlers: dict[str, Handler] = {}
        self.default_handler: Optional[Handler] = None
        self.wall_post_handler: Optional[WallPostHandler] = None

    def command(self, cmd: str):
        def decorator(func: Handler):
            self.command_handlers[cmd] = func
            return func

        return decorator

    def command_prefix(self, prefix: str):
        def decorator(func: Handler):
            self.prefix_handlers.append((prefix, func))
            return func

        return decorator

    def state_handler(self, state: str):
        def decorator(func: Handler):
            self.state_handlers[state] = func
            return func

        return decorator

    def default(self, func: Handler):
        self.default_handler = func
        return func

    def wall_post(self, func: WallPostHandler):
        self.wall_post_handler = func
        return func

    async def handle_update(self, update: dict[str, Any], api: Optional[VKApi] = None, guard_only: bool = False) -> None:
        active_api = api or self.api
        if update.get("type") == "wall_post_new":
            if self.wall_post_handler:
                await self.wall_post_handler(active_api, update)
            return
        if update.get("type") != "message_new":
            return
        message = update.get("object", {}).get("message") or update.get("object") or {}
        if not message or message.get("out"):
            return
        if guard_only and int(message.get("peer_id") or 0) <= 2_000_000_000:
            return

        ctx = Ctx(api=active_api, state=self.state, update=update, message=message)
        if not await is_new_message(active_api, message):
            logger.info(
                "Skipping duplicate VK message peer_id=%s conversation_message_id=%s",
                ctx.peer_id,
                message.get("conversation_message_id") or message.get("id"),
            )
            return

        text = normalize_text_command(ctx.text)
        cmd = ctx.cmd or TEXT_COMMAND_ALIASES.get(text, "")
        logger.info("Incoming VK message peer_id=%s user_id=%s text=%r cmd=%r", ctx.peer_id, ctx.user_id, ctx.text, cmd)

        try:
            if guard_only:
                if cmd == "check_subs":
                    await self.command_handlers["check_subs"](ctx)
                elif not cmd and self.default_handler:
                    await self.default_handler(ctx)
                return

            if cmd == "start" or text in {"/start", "start", "начать"}:
                await self.command_handlers["start"](ctx)
                return
            if text == "/id":
                await ctx.answer(f"VK peer_id: {ctx.peer_id}\nVK user_id: {ctx.user_id}")
                return
            if cmd:
                handler = self.command_handlers.get(cmd)
                if handler:
                    await handler(ctx)
                else:
                    for prefix, prefix_handler in self.prefix_handlers:
                        if cmd.startswith(prefix):
                            await prefix_handler(ctx)
                            break
                    else:
                        await ctx.answer("Команда пока не обработана.")
                return

            state = self.state.get_state(ctx.user_id)
            if state and state in self.state_handlers:
                await self.state_handlers[state](ctx)
                return

            if self.default_handler:
                await self.default_handler(ctx)
        except Exception:
            logger.exception("Failed to handle VK update")
            try:
                await ctx.answer("Произошла ошибка. Я записал ее в лог, можно повторить действие или вернуться в меню.")
            except Exception:
                logger.exception("Failed to notify user about handler error")

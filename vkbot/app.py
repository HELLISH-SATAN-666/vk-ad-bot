from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

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


class VKBotApp:
    def __init__(self, api: VKApi):
        self.api = api
        self.state = StateStore()
        self.command_handlers: dict[str, Handler] = {}
        self.prefix_handlers: list[tuple[str, Handler]] = []
        self.state_handlers: dict[str, Handler] = {}
        self.default_handler: Optional[Handler] = None

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

    async def handle_update(self, update: dict[str, Any]) -> None:
        if update.get("type") != "message_new":
            return
        message = update.get("object", {}).get("message") or update.get("object") or {}
        if not message or message.get("out"):
            return

        ctx = Ctx(api=self.api, state=self.state, update=update, message=message)
        text = ctx.text.lower()
        cmd = ctx.cmd

        try:
            if text in {"/start", "start", "начать"}:
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

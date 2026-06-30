from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from os import getenv
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import aiohttp


logger = logging.getLogger(__name__)


class VKApiError(RuntimeError):
    def __init__(self, method: str, error: dict[str, Any]):
        self.method = method
        self.error = error
        super().__init__(f"VK API {method}: {error.get('error_code')} {error.get('error_msg')}")


@dataclass
class VKGroupInfo:
    id: int
    screen_name: str
    name: str


class VKApi:
    def __init__(self, token: str, group_id: Optional[int] = None, api_version: Optional[str] = None):
        self.token = token
        self.group_id = group_id
        self.api_version = api_version or getenv("VK_API_VERSION", "5.199")
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def open(self) -> None:
        if not self.session:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))

    async def close(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None

    async def call(self, method: str, **params) -> Any:
        await self.open()
        params["access_token"] = self.token
        params["v"] = self.api_version
        url = f"https://api.vk.com/method/{method}"
        assert self.session is not None
        async with self.session.post(url, data=params) as response:
            data = await response.json(content_type=None)
        if "error" in data:
            raise VKApiError(method, data["error"])
        return data.get("response")

    async def get_own_group(self) -> VKGroupInfo:
        response = await self.call("groups.getById", fields="screen_name")
        groups = response.get("groups") if isinstance(response, dict) else response
        group = groups[0]
        self.group_id = int(group["id"])
        return VKGroupInfo(id=int(group["id"]), screen_name=group.get("screen_name", ""), name=group.get("name", ""))

    async def enable_long_poll(self, group_id: Optional[int] = None) -> None:
        gid = group_id or self.group_id
        if not gid:
            raise RuntimeError("VK group id is not configured")
        await self.call(
            "groups.setLongPollSettings",
            group_id=gid,
            enabled=1,
            message_new=1,
            message_reply=1,
            message_event=1,
            message_allow=1,
            message_deny=1,
            wall_post_new=0,
        )

    async def send_message(
        self,
        peer_id: int,
        message: str,
        keyboard: Optional[str] = None,
        attachment: Optional[str] = None,
        disable_mentions: bool = True,
    ) -> int:
        params = {
            "message": message[:4096],
            "random_id": random.randint(1, 2_147_483_647),
            "disable_mentions": 1 if disable_mentions else 0,
        }
        use_peer_ids = bool(self.group_id) and is_vk_chat_peer_id(peer_id)
        if use_peer_ids:
            params["peer_ids"] = int(peer_id)
        else:
            params["peer_id"] = int(peer_id)
        if keyboard:
            params["keyboard"] = keyboard
        if attachment:
            params["attachment"] = attachment
        try:
            response = await self.call("messages.send", **params)
        except VKApiError as exc:
            if keyboard and exc.error.get("error_code") == 912:
                logger.warning("VK rejected keyboard for peer_id=%s; retrying without keyboard", peer_id)
                params.pop("keyboard", None)
                response = await self.call("messages.send", **params)
            else:
                raise
        return self._sent_message_id(response, int(peer_id))

    @staticmethod
    def _sent_message_id(response: Any, peer_id: int) -> int:
        if isinstance(response, list):
            item = next(
                (value for value in response if isinstance(value, dict) and int(value.get("peer_id") or 0) == int(peer_id)),
                response[0] if response else None,
            )
            if isinstance(item, dict):
                return int(item.get("conversation_message_id") or item.get("message_id") or 0)
            return 0
        if isinstance(response, dict):
            return int(response.get("conversation_message_id") or response.get("message_id") or 0)
        return int(response or 0)

    async def edit_message(self, peer_id: int, message_id: int, message: str, keyboard: Optional[str] = None) -> None:
        params = {"peer_id": peer_id, "conversation_message_id": message_id, "message": message[:4096]}
        if keyboard:
            params["keyboard"] = keyboard
        await self.call("messages.edit", **params)

    async def delete_message(
        self,
        message_id: int,
        delete_for_all: bool = True,
        peer_id: Optional[int] = None,
        conversation_message_id: Optional[int] = None,
    ) -> None:
        params = {"delete_for_all": 1 if delete_for_all else 0}
        if peer_id and conversation_message_id:
            params["peer_id"] = int(peer_id)
            params["cmids"] = int(conversation_message_id)
        else:
            params["message_ids"] = int(message_id)
        try:
            await self.call("messages.delete", **params)
        except VKApiError:
            if peer_id and conversation_message_id and "cmids" in params:
                params["conversation_message_ids"] = params.pop("cmids")
                try:
                    await self.call("messages.delete", **params)
                    return
                except VKApiError:
                    params.pop("conversation_message_ids", None)
                    params.pop("peer_id", None)
                    params["message_ids"] = int(message_id)
                    await self.call("messages.delete", **params)
                    return
            raise

    async def delete_message_everywhere(self, message_id: int, peer_id: int, delete_for_all: bool = True) -> None:
        base = {"delete_for_all": 1 if delete_for_all else 0}
        variants = [
            {**base, "message_ids": int(message_id)},
            {**base, "peer_id": int(peer_id), "cmids": int(message_id)},
            {**base, "peer_id": int(peer_id), "conversation_message_ids": int(message_id)},
        ]
        last_error: VKApiError | None = None
        success_count = 0
        for params in variants:
            try:
                await self.call("messages.delete", **params)
                success_count += 1
            except VKApiError as exc:
                last_error = exc
                logger.debug("VK delete variant failed: %s params=%s", exc, params)
        if success_count == 0 and last_error is not None:
            raise last_error

    async def wall_post(self, owner_id: int, message: str, attachments: Optional[str] = None, from_group: bool = True) -> int:
        params: dict[str, Any] = {
            "owner_id": -abs(int(owner_id)),
            "message": message[:4096],
            "from_group": 1 if from_group else 0,
        }
        if attachments:
            params["attachments"] = attachments
        response = await self.call("wall.post", **params)
        if isinstance(response, dict):
            return int(response.get("post_id") or 0)
        return int(response or 0)

    async def delete_wall_post(self, owner_id: int, post_id: int) -> None:
        await self.call("wall.delete", owner_id=-abs(int(owner_id)), post_id=int(post_id))

    async def get_user_name(self, user_id: int) -> str:
        response = await self.call("users.get", user_ids=user_id)
        if not response:
            return str(user_id)
        user = response[0]
        return f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or str(user_id)

    async def resolve_group(self, raw: str) -> VKGroupInfo:
        name = normalize_group_ref(raw)
        if name.lstrip("-").isdigit():
            group_id = abs(int(name))
            response = await self.call("groups.getById", group_ids=group_id, fields="screen_name")
            groups = response.get("groups") if isinstance(response, dict) else response
            group = groups[0]
            return VKGroupInfo(id=int(group["id"]), screen_name=group.get("screen_name", ""), name=group.get("name", ""))

        resolved = await self.call("utils.resolveScreenName", screen_name=name)
        if not resolved or resolved.get("type") not in {"group", "page"}:
            raise ValueError("Не найдено VK-сообщество")
        group_id = int(resolved["object_id"])
        response = await self.call("groups.getById", group_ids=group_id, fields="screen_name")
        groups = response.get("groups") if isinstance(response, dict) else response
        group = groups[0]
        return VKGroupInfo(id=int(group["id"]), screen_name=group.get("screen_name", ""), name=group.get("name", ""))

    async def group_title(self, group_id: int) -> str:
        if is_vk_chat_peer_id(group_id):
            return await self.chat_title(group_id)
        try:
            response = await self.call("groups.getById", group_ids=abs(int(group_id)), fields="screen_name")
            groups = response.get("groups") if isinstance(response, dict) else response
            group = groups[0]
            return group.get("name") or group.get("screen_name") or str(group_id)
        except Exception:
            return str(group_id)

    async def chat_title(self, peer_id: int) -> str:
        try:
            response = await self.call("messages.getConversationsById", peer_ids=int(peer_id))
            items = response.get("items", []) if isinstance(response, dict) else []
            if not items:
                return f"VK chat {peer_id}"
            conversation = items[0].get("conversation") or items[0]
            chat_settings = conversation.get("chat_settings") or {}
            return chat_settings.get("title") or f"VK chat {peer_id}"
        except Exception:
            return f"VK chat {peer_id}"

    async def is_chat_member(self, peer_id: int, user_id: int) -> bool:
        response = await self.call("messages.getConversationMembers", peer_id=int(peer_id))
        items = response.get("items", []) if isinstance(response, dict) else []
        for item in items:
            member_id = item.get("member_id") if isinstance(item, dict) else item
            if int(member_id or 0) == int(user_id):
                return True
        return False

    async def chat_invite_link(self, peer_id: int) -> Optional[str]:
        response = await self.call("messages.getInviteLink", peer_id=int(peer_id))
        if isinstance(response, dict):
            return response.get("link")
        if isinstance(response, str):
            return response
        return None

    async def target_link(self, group_id: int) -> str:
        if is_vk_chat_peer_id(group_id):
            try:
                link = await self.chat_invite_link(group_id)
                if link:
                    return link
            except Exception:
                logger.exception("Failed to get VK chat invite link for peer_id=%s", group_id)
            chat_id = int(group_id) - 2_000_000_000
            return f"https://vk.com/im?sel=c{chat_id}"
        return f"https://vk.com/club{abs(int(group_id))}"

    async def is_group_member(self, group_id: int, user_id: int) -> bool:
        if is_vk_chat_peer_id(group_id):
            return await self.is_chat_member(group_id, user_id)
        response = await self.call("groups.isMember", group_id=abs(int(group_id)), user_id=user_id)
        if isinstance(response, dict):
            return bool(response.get("member"))
        return bool(response)

    async def is_group_manager(self, user_id: int, group_id: Optional[int] = None) -> bool:
        gid = group_id or self.group_id
        if not gid:
            return False
        response = await self.call("groups.getMembers", group_id=abs(int(gid)), filter="managers")
        items = response.get("items", []) if isinstance(response, dict) else []
        for item in items:
            if isinstance(item, dict) and int(item.get("id") or 0) == int(user_id):
                return True
            if not isinstance(item, dict) and int(item) == int(user_id):
                return True
        return False

    async def get_group_managers(self, group_id: Optional[int] = None) -> list[int]:
        gid = group_id or self.group_id
        if not gid:
            return []
        response = await self.call("groups.getMembers", group_id=abs(int(gid)), filter="managers")
        items = response.get("items", []) if isinstance(response, dict) else []
        managers: list[int] = []
        for item in items:
            try:
                managers.append(int(item.get("id") if isinstance(item, dict) else item))
            except (TypeError, ValueError):
                continue
        return managers

class VKLongPoll:
    def __init__(self, api: VKApi, group_id: int):
        self.api = api
        self.group_id = group_id
        self.server = ""
        self.key = ""
        self.ts = ""

    async def refresh(self) -> None:
        response = await self.api.call("groups.getLongPollServer", group_id=self.group_id)
        self.server = response["server"]
        self.key = response["key"]
        self.ts = response["ts"]

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        await self.refresh()
        while True:
            try:
                await self.api.open()
                assert self.api.session is not None
                async with self.api.session.get(
                    self.server,
                    params={
                        "act": "a_check",
                        "key": self.key,
                        "ts": self.ts,
                        "wait": 25,
                        "mode": 2,
                        "version": 3,
                    },
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as response:
                    data = await response.json(content_type=None)

                if data.get("failed"):
                    await self.refresh()
                    continue

                self.ts = data["ts"]
                for update in data.get("updates", []):
                    yield update
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("VK long poll connection failed: %s", exc)
                await asyncio.sleep(3)
            except Exception:
                logger.exception("VK long poll unexpected error")
                await asyncio.sleep(3)
                await self.refresh()


def normalize_group_ref(raw: str) -> str:
    value = raw.strip()
    value = value.split()[0]
    parsed = urlparse(value if "://" in value else f"https://{value}" if value.startswith(("vk.com/", "m.vk.com/")) else "")
    if parsed.netloc in {"vk.com", "www.vk.com", "m.vk.com"}:
        value = parsed.path
    value = value.replace("https://vk.com/", "").replace("http://vk.com/", "").replace("vk.com/", "").replace("m.vk.com/", "")
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = value.strip("/")
    if value.startswith("@"):
        value = value[1:]
    if value.startswith("club") and value[4:].isdigit():
        return value[4:]
    if value.startswith("public") and value[6:].isdigit():
        return value[6:]
    if value.startswith("event") and value[5:].isdigit():
        return value[5:]
    return value


def is_vk_chat_peer_id(value: int | str) -> bool:
    try:
        return int(value) > 2_000_000_000
    except (TypeError, ValueError):
        return False


def parse_payload(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def attachment_to_vk_id(attachments: list[dict[str, Any]]) -> Optional[str]:
    if not attachments:
        return None
    first = attachments[0]
    media_type = first.get("type")
    if media_type not in {"photo", "video", "doc"}:
        return None
    media = first.get(media_type) or {}
    owner_id = media.get("owner_id")
    media_id = media.get("id")
    access_key = media.get("access_key")
    if not owner_id or not media_id:
        return None
    attach = f"{media_type}{owner_id}_{media_id}"
    if access_key:
        attach += f"_{access_key}"
    return attach

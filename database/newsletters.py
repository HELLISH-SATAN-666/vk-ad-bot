from datetime import date, time
from enum import IntEnum
from typing import Optional, Literal

from asyncpg import Record

from .base import Database


class NewslettersTarget(IntEnum):
    ADVERTISERS = 1
    PARTNERS = 2
    SUBS = 3
    SUB = 4
    PARTNERS_AND_SUBS = 5


class Newsletters(Database):
    def __init__(self):
        super().__init__()

    async def add(self, creator_id: int, text: str, target: NewslettersTarget, expires_at: Optional[date] = None, file_id: str = None, file_format: Literal["video", "photo"] = None):
        await self.connect()

        is_moderating = True if expires_at else None

        await self.execute(
            "INSERT INTO newsletters(creator_id, text, target, expires_at, is_moderating, file_id) VALUES ($1, $2, $3, $4, $5, $6);",
            creator_id, text, int(target), expires_at, is_moderating, (file_id if file_id and file_id.startswith(("photo", "video", "doc")) else (file_format + file_id) if file_format else None)
        )

        return self

    async def get_all(self, is_sub=False, is_moderating=None) -> Record:
        query_exp_moder = f"AND is_moderating = {is_moderating}" if is_moderating is not None else ""
        query_exp = f"IS NOT NULL {query_exp_moder}" if is_sub else "IS NULL"

        group_info = await self.fetch(
            f"SELECT * FROM newsletters WHERE expires_at {query_exp} ORDER BY id;",
        )

        return group_info

    async def get_by_id(self, nl_id: int) -> Optional[Record]:
        return await self.fetchrow(
            "SELECT * FROM newsletters WHERE id = $1;",
            nl_id,
        )

    async def get_current_sub_nls(self):
        now = self.now_msk.time().replace(second=0, microsecond=0)
        group_info = await self.fetch(
            f"SELECT * FROM newsletters WHERE expires_at IS NOT NULL AND is_moderating = FALSE AND send_time = $1;",
            now
        )
        # print(f"Подписок после проерки {len(group_info)} на время {now}")
        return group_info


    async def delete(self, nl_id: int):
        await self.execute(
            "DELETE FROM newsletters WHERE id = $1;",
            nl_id
        )

    async def update_send_time(self, nl_id: int, send_time: time):
        await self.execute(
            "UPDATE newsletters SET send_time = $1 WHERE id = $2;",
            send_time, nl_id
        )

    async def update_expires_date(self, nl_id: int, expires_date: date):
        await self.execute(
            "UPDATE newsletters SET expires_at = $1 WHERE id = $2;",
            expires_date, nl_id
        )

    async def moderate(self, nl_id: int):
        await self.execute(
            "UPDATE newsletters SET is_moderating = $1 WHERE id = $2;",
            False, nl_id
        )

    async def delete_expired(self):
        await self.execute(
            "DELETE FROM newsletters WHERE expires_at < $1",
            self.now_msk.date()
        )

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from asyncpg import Record

from .base import Database


class UsersSubs(Database):
    async def add_sub(
        self,
        user_id: int,
        group_id: int,
        rate_type: Literal["time", "msg", "sub_join", "sub_msg"],
        expires_at: Optional[datetime] = None,
        msg_left: Optional[int] = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO users_subs_info(user_id, group_id, type, expires_at, msg_left)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING;
            """,
            user_id,
            group_id,
            rate_type,
            expires_at,
            msg_left,
        )

    async def in_db(self, user_id: int, group_id: int) -> bool:
        value = await self.fetchval(
            "SELECT id FROM users_subs_info WHERE user_id = $1 AND group_id = $2;",
            user_id,
            group_id,
        )
        return bool(value)

    async def get_sub(self, user_id: int, group_id: int) -> Optional[Record]:
        return await self.fetchrow(
            "SELECT * FROM users_subs_info WHERE user_id = $1 AND group_id = $2;",
            user_id,
            group_id,
        )

    async def upsert_time_sub(self, user_id: int, group_id: int, expires_at: datetime) -> None:
        await self.execute(
            """
            INSERT INTO users_subs_info(user_id, group_id, type, expires_at, msg_left)
            VALUES ($1, $2, 'time', $3, NULL)
            ON CONFLICT (user_id, group_id) DO UPDATE SET
                type = 'time',
                expires_at = EXCLUDED.expires_at,
                msg_left = NULL;
            """,
            user_id,
            group_id,
            expires_at,
        )

    async def add_msg_sub(self, user_id: int, group_id: int, msg_count: int) -> None:
        await self.execute(
            """
            INSERT INTO users_subs_info(user_id, group_id, type, expires_at, msg_left)
            VALUES ($1, $2, 'msg', NULL, $3)
            ON CONFLICT (user_id, group_id) DO UPDATE SET
                type = 'msg',
                expires_at = NULL,
                msg_left = COALESCE(users_subs_info.msg_left, 0) + EXCLUDED.msg_left;
            """,
            user_id,
            group_id,
            msg_count,
        )

    async def reduce_msg_left(self, user_id: int, group_id: int) -> Optional[int]:
        return await self.fetchval(
            """
            UPDATE users_subs_info
            SET msg_left = msg_left - 1
            WHERE user_id = $1 AND group_id = $2 AND type = 'msg' AND msg_left IS NOT NULL
            RETURNING msg_left;
            """,
            user_id,
            group_id,
        )

    async def remove_sub(self, user_id: int, group_id: int) -> None:
        await self.execute(
            "DELETE FROM users_subs_info WHERE user_id = $1 AND group_id = $2;",
            user_id,
            group_id,
        )

    async def cascade_remove_subs(self, group_id: int) -> None:
        await self.execute("DELETE FROM users_subs_info WHERE group_id = $1;", group_id)

    async def count(self) -> int:
        return await self.fetchval("SELECT COUNT(*) FROM users_subs_info;")

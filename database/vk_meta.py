from __future__ import annotations

from typing import Optional

from asyncpg import Record

from .base import Database


class VkGroups(Database):
    async def upsert(
        self,
        group_id: int,
        title: Optional[str] = None,
        screen_name: Optional[str] = None,
        token: Optional[str] = None,
        target_type: str = "community",
        can_wall_post: Optional[bool] = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO vk_groups(group_id, title, screen_name, token, target_type, can_wall_post)
            VALUES ($1, $2, $3, $4, $5, COALESCE($6, FALSE))
            ON CONFLICT (group_id) DO UPDATE SET
                title = COALESCE(EXCLUDED.title, vk_groups.title),
                screen_name = COALESCE(EXCLUDED.screen_name, vk_groups.screen_name),
                token = COALESCE(EXCLUDED.token, vk_groups.token),
                target_type = COALESCE(EXCLUDED.target_type, vk_groups.target_type),
                can_wall_post = COALESCE(EXCLUDED.can_wall_post, vk_groups.can_wall_post);
            """,
            group_id,
            title,
            screen_name,
            token,
            target_type,
            can_wall_post,
        )

    async def get(self, group_id: int) -> Optional[Record]:
        return await self.fetchrow("SELECT * FROM vk_groups WHERE group_id = $1;", group_id)

    async def delete(self, group_id: int) -> None:
        await self.execute("DELETE FROM vk_groups WHERE group_id = $1;", group_id)


async def apply_vk_schema() -> None:
    db = Database()
    await db.connect()
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS vk_groups (
            id SERIAL PRIMARY KEY,
            group_id BIGINT UNIQUE NOT NULL,
            title TEXT,
            screen_name TEXT,
            token TEXT,
            target_type TEXT DEFAULT 'community',
            can_wall_post BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

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
                can_wall_post = CASE
                    WHEN $6::boolean IS NULL THEN vk_groups.can_wall_post
                    ELSE EXCLUDED.can_wall_post
                END;
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

    async def get_partner_long_poll_groups(self, statuses: list[int]) -> list[Record]:
        return await self.fetch(
            """
            SELECT DISTINCT vg.group_id, vg.token, vg.title, vg.screen_name
            FROM vk_groups vg
            JOIN partner_groups pg ON abs(pg.group_id) = vg.group_id
            WHERE COALESCE(NULLIF(vg.token, ''), '') <> ''
              AND vg.target_type = 'community'
              AND pg.partner_type = ANY($1::smallint[]);
            """,
            statuses,
        )

    async def delete(self, group_id: int) -> None:
        await self.execute("DELETE FROM vk_groups WHERE group_id = $1;", group_id)


class VkProcessedMessages(Database):
    async def mark(self, group_id: int, peer_id: int, conversation_message_id: int, vk_message_id: Optional[int] = None) -> bool:
        row_id = await self.fetchval(
            """
            INSERT INTO vk_processed_messages(group_id, peer_id, conversation_message_id, vk_message_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (group_id, peer_id, conversation_message_id) DO NOTHING
            RETURNING id;
            """,
            group_id,
            peer_id,
            conversation_message_id,
            vk_message_id,
        )
        return row_id is not None


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
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS vk_processed_messages (
            id BIGSERIAL PRIMARY KEY,
            group_id BIGINT NOT NULL,
            peer_id BIGINT NOT NULL,
            conversation_message_id BIGINT NOT NULL,
            vk_message_id BIGINT,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS vk_processed_messages_uidx
        ON vk_processed_messages(group_id, peer_id, conversation_message_id);
        """
    )

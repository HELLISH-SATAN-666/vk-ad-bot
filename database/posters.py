from argparse import ArgumentError
from typing import Literal, Optional
from enum import IntEnum

from asyncpg import Record

from .base import Database

class PosterStatus(IntEnum):
    DELETED = -1
    MODERATED = 0
    ACTIVE = 1
    FROZEN = 2



class Posters(Database):
    def __init__(self):
        super().__init__()

    async def add(self, creator_id: int, topic_id: int, region_codes: list[int], text: str, file_id: str, file_format: Literal["video", "photo", "none"], end_date) -> int:
        await self.connect()

        db_file_id = None
        if file_id:
            db_file_id = file_id if file_id.startswith(("photo", "video", "doc")) else file_format + file_id

        poster_id = await self.fetchval(
            "INSERT INTO posters(file_id, topic_id, region_codes, text, creator_id, end_date) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id;",
            db_file_id, topic_id, region_codes, text, creator_id, end_date
        )

        return int(poster_id)

    async def change_button_name(self, poster_id: int, new_button_name: str):
        await self.execute(
            "UPDATE posters SET referral_button_name = $1 WHERE id = $2",
            new_button_name, poster_id
        )

    async def change_status(self, poster_id: int,  new_status: PosterStatus):
        await self.execute(
            "UPDATE posters SET status = $1 WHERE id = $2",
            new_status.value, poster_id
        )

    async def get_by_status(self, status: PosterStatus, creator_id: Optional[int] = None) -> list[Record]:
        query = "SELECT * FROM posters WHERE status = $1"
        args = [int(status)]
        if creator_id is not None:
            query += " AND creator_id = $2"
            args.append(creator_id)
        query += " ORDER BY id;"

        posters = await self.fetch(query, *args)

        return posters

    async def get_by_id(self, poster_id) -> list[Record]:
        posters = await self.fetchrow(
            "SELECT * FROM posters WHERE id = $1;",
            poster_id
        )

        return posters

    async def get_all(self):
        posters = await self.fetch(
            "SELECT * FROM posters ORDER BY status;",
        )

        return posters

    async def in_db(self, user_id: int) -> bool:
        advertiser_id = await self.fetchval(
            "SELECT id FROM posters WHERE creator_id = $1;",
            user_id
        )

        return bool(advertiser_id)

    async def find_active_poster_ids(self, poster_ids: list[int]):
        active_poster_ids = []
        for poster_id in poster_ids:
            status = await self.fetchval(
                "SELECT status FROM posters WHERE id = $1;",
                poster_id
            )

            if status == PosterStatus.ACTIVE: active_poster_ids.append(poster_id)

        return active_poster_ids

    async def delete_cascade(self, poster_ids: list[int]):
        await self.execute("""
                UPDATE partner_groups
                SET show_ad_ids = ARRAY(
                    SELECT x
                    FROM unnest(show_ad_ids) AS x
                    WHERE x != ALL($1::integer[])
                )
                WHERE show_ad_ids && $1::integer[];
            """, poster_ids)

    async def delete(self, poster_ids: int | list[int]):
        if isinstance(poster_ids, int):
           poster_ids = [poster_ids]

        await self.fetch(
            "DELETE FROM posters WHERE id = ANY($1)",
            poster_ids
        )

        await self.delete_cascade(poster_ids)

    async def delete_expired(self):
        expired_posters = await self.fetch(
            "SELECT id FROM posters WHERE end_date < $1 AND status = $2",
            self.now_msk.date(), int(PosterStatus.ACTIVE)
        )

        expired_poster_ids = [p['id'] for p in expired_posters]

        await self.delete(expired_poster_ids)

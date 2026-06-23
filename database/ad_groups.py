from enum import IntEnum
from typing import Optional

from asyncpg import Record

from .base import Database

class AdGroupsStatus(IntEnum):
    DELETED = -1
    MODERATED = 0
    ACTIVE = 1
    FROZEN = 2


class AdGroups(Database):
    def __init__(self):
        super().__init__()

    async def add(self, creator_id: int, group_id: int, end_date):
        await self.connect()

        await self.execute(
            "INSERT INTO ad_groups(group_id, end_date, creator_id) VALUES ($1, $2, $3);",
            group_id, end_date, creator_id
        )

        return self

    async def change_status(self, ad_group_id: int,  new_status: AdGroupsStatus, by_group_id: bool = False):
        field = "group_id" if by_group_id else "id"
        await self.execute(
            f"UPDATE ad_groups SET status = $1 WHERE {field} = $2",
            int(new_status), ad_group_id
        )

    async def get_by_status(self, status: AdGroupsStatus, creator_id: Optional[int] = None) -> list[Record]:
        add_query = ";"
        if creator_id:
            add_query = f" AND creator_id = $2;"

        ad_groups = await self.fetch(
            "SELECT * FROM ad_groups WHERE status = $1" + add_query,
            int(status), creator_id
        )

        return ad_groups

    async def get_all(self) -> list[Record]:
        ad_groups = await self.fetch(
            "SELECT * FROM ad_groups WHERE status != $1 ORDER BY status",
            int(AdGroupsStatus.DELETED)
        )

        return ad_groups

    async def get_by_db_id(self, ad_group_id: int) -> Record:
        ad_group = await self.fetchrow(
            "SELECT * FROM ad_groups WHERE id = $1",
            ad_group_id
        )

        return ad_group

    async def delete_cascade(self, group_db_ids: list[int]):
        await self.execute("""
                    UPDATE partner_groups
                    SET need_groups = ARRAY(
                        SELECT x
                        FROM unnest(need_groups) AS x
                        WHERE x != ALL($1::bigint[])
                    )
                    WHERE need_groups && $1::bigint[];
                """, group_db_ids)

    async def delete(self, group_db_ids: int | list[int]):
        if isinstance(group_db_ids, int):
           group_db_ids = [group_db_ids]

        group_ids = await self.fetch(
            "SELECT group_id FROM ad_groups WHERE id = ANY($1::integer[])",
            group_db_ids
        )

        await self.execute(
            "DELETE FROM ad_groups WHERE id = ANY($1::integer[])",
            group_db_ids
        )

        await self.delete_cascade([row["group_id"] for row in group_ids])

    async def delete_expired(self):
        expired_groups = await self.fetch(
            "SELECT id FROM ad_groups WHERE end_date < $1 AND status = $2",
            self.now_msk.date(), int(AdGroupsStatus.ACTIVE)
        )

        expired_groups_db_ids = [g['id'] for g in expired_groups]

        await self.delete(expired_groups_db_ids)

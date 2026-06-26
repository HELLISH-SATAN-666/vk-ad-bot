from enum import IntEnum
from json import dumps, loads
from typing import Any

from asyncpg import Record

from .ad_groups import AdGroupsStatus
from .base import Database

class PartnerTypes(IntEnum):
    # В данной группе не реализована ни включена ни одна из функций
    FROZEN = 0
    # В данной группе включен вход по подписке на другие группы
    SUB_GROUPS = 1
    # В данной группе только показывается купленная реклама
    PROMOTION = 2
    PROMOTION_AND_SUB = 3


DEFAULT_SUB_RATES = {
    "msg": [
        {"msg": 3, "price_rub": 20},
        {"msg": 8, "price_rub": 40},
        {"msg": 16, "price_rub": 120},
        {"msg": 28, "price_rub": 180},
        {"msg": 120, "price_rub": 500},
    ],
    "time": [
        {"days": 7, "price_rub": 20},
        {"days": 30, "price_rub": 50},
        {"days": 90, "price_rub": 120},
        {"days": 180, "price_rub": 200},
        {"days": 365, "price_rub": 300},
    ],
    "none": [],
}


def normalize_sub_rates(raw: Any) -> dict[str, list[dict[str, int]]]:
    if not raw:
        return {key: list(value) for key, value in DEFAULT_SUB_RATES.items()}
    rates = loads(raw) if isinstance(raw, str) else raw
    result = {key: list(value) for key, value in DEFAULT_SUB_RATES.items()}
    for key in ("msg", "time", "none"):
        value = rates.get(key) if isinstance(rates, dict) else None
        if isinstance(value, list):
            result[key] = value
    return result


class PartnerGroups(Database):
    def __init__(self):
        super().__init__()

    async def add(self, creator_id: int, group_id: int, region_codes: list[int], selected_categories: list[str], partner_type: PartnerTypes = PartnerTypes.PROMOTION):
        await self.connect()

        await self.execute(
            "DELETE FROM partner_groups WHERE group_id = $1;",
            group_id
        )

        await self.execute(
            "INSERT INTO partner_groups(group_id, creator_id, region_codes, poster_categories, partner_type) VALUES ($1, $2, $3, $4, $5);",
            group_id, creator_id, region_codes, list(map(int, selected_categories)), int(partner_type)
        )
        return self

    async def add_need_groups(self, main_group_id: int, group_ids: list[int] | int):
        if isinstance(group_ids, int):
            group_ids = [group_ids]
        await self.execute("""
            UPDATE partner_groups
            SET
                need_groups = (
                    SELECT ARRAY(
                        SELECT DISTINCT x
                        FROM unnest(COALESCE(need_groups, '{}')::bigint[] || $1::bigint[]) AS x
                    )
                ),
                partner_type = CASE
                    WHEN partner_type = $3 THEN $4
                    ELSE partner_type
                END
            WHERE group_id = $2;
            """,
            group_ids,
            main_group_id,
            int(PartnerTypes.PROMOTION),
            int(PartnerTypes.PROMOTION_AND_SUB),
        )

    async def add_posters(self, main_group_id: int, ad_poster_ids: list[int] | int):
        if isinstance(ad_poster_ids, int):
            ad_poster_ids = [ad_poster_ids]

        await self.execute("""
            UPDATE partner_groups
            SET show_ad_ids = (
                SELECT ARRAY(
                    SELECT DISTINCT x
                    FROM unnest(show_ad_ids || $1::integer[]) AS x
                )
            )
            WHERE group_id = $2;
            """,
            ad_poster_ids, main_group_id
        )

    async def replace_poster_groups(self, poster_id: int, group_ids: list[int]) -> None:
        await self.execute("""
            UPDATE partner_groups
            SET show_ad_ids = ARRAY(
                SELECT x
                FROM unnest(COALESCE(show_ad_ids, '{}')::integer[]) AS x
                WHERE x <> $1
            )
            WHERE $1 = ANY(COALESCE(show_ad_ids, '{}')::integer[]);
            """,
            poster_id,
        )
        if not group_ids:
            return

        await self.execute("""
            UPDATE partner_groups
            SET show_ad_ids = (
                SELECT ARRAY(
                    SELECT DISTINCT x
                    FROM unnest(COALESCE(show_ad_ids, '{}')::integer[] || ARRAY[$1]::integer[]) AS x
                )
            )
            WHERE group_id = ANY($2::bigint[]);
            """,
            poster_id, group_ids,
        )

    async def replace_need_groups(self, ad_group_id: int, partner_group_ids: list[int]) -> None:
        await self.execute("""
            UPDATE partner_groups
            SET need_groups = ARRAY(
                SELECT x
                FROM unnest(COALESCE(need_groups, '{}')::bigint[]) AS x
                WHERE x <> $1
            )
            WHERE $1 = ANY(COALESCE(need_groups, '{}')::bigint[]);
            """,
            ad_group_id,
        )
        if not partner_group_ids:
            return

        await self.execute("""
            UPDATE partner_groups
            SET need_groups = (
                SELECT ARRAY(
                    SELECT DISTINCT x
                    FROM unnest(COALESCE(need_groups, '{}')::bigint[] || ARRAY[$1]::bigint[]) AS x
                )
            )
            WHERE group_id = ANY($2::bigint[]);
            """,
            ad_group_id, partner_group_ids,
        )

    async def replace_group_need_groups(self, main_group_id: int, need_group_ids: list[int]) -> None:
        await self.execute(
            """
            UPDATE partner_groups
            SET
                need_groups = $1::bigint[],
                partner_type = CASE
                    WHEN cardinality($1::bigint[]) > 0 AND partner_type = $3 THEN $4
                    ELSE partner_type
                END
            WHERE group_id = $2;
            """,
            need_group_ids,
            main_group_id,
            int(PartnerTypes.PROMOTION),
            int(PartnerTypes.PROMOTION_AND_SUB),
        )

    async def change_sub_rate_type(self, main_group_id: int, rate_type: str) -> None:
        if rate_type not in ("none", "time", "msg"):
            raise ValueError(f"Unsupported subscription rate type: {rate_type}")
        await self.execute(
            """
            UPDATE partner_groups
            SET
                sub_rate_type = $1,
                partner_type = CASE
                    WHEN $1 <> 'none' AND partner_type = $3 THEN $4
                    ELSE partner_type
                END
            WHERE group_id = $2;
            """,
            rate_type,
            main_group_id,
            int(PartnerTypes.PROMOTION),
            int(PartnerTypes.PROMOTION_AND_SUB),
        )

    async def replace_sub_rates(self, main_group_id: int, rate_type: str, rates: list[dict[str, int]]) -> None:
        if rate_type not in ("time", "msg"):
            raise ValueError(f"Unsupported subscription rate type: {rate_type}")
        current = normalize_sub_rates(
            await self.fetchval("SELECT sub_rates FROM partner_groups WHERE group_id = $1;", main_group_id)
        )
        current[rate_type] = rates
        await self.execute(
            "UPDATE partner_groups SET sub_rates = $1::jsonb WHERE group_id = $2;",
            dumps(current, ensure_ascii=False),
            main_group_id,
        )

    async def change_status(self, new_status: PartnerTypes, db_group_id: int = None, main_group_id: int = None):
        if not (db_group_id or main_group_id):
            raise TypeError("При изменении статуса необходимо указать либо id по бд либо id группы из телеграмм")

        field_name, field_value = ("group_id", main_group_id) if main_group_id else ("id", db_group_id)


        await self.execute(
            f"UPDATE partner_groups SET partner_type = $1 WHERE {field_name} = $2",
            int(new_status), field_value
        )

    async def get_all_ids(self, status: PartnerTypes = PartnerTypes.SUB_GROUPS) -> list[int]:
        if not isinstance(status, list):
            status = [status, PartnerTypes.PROMOTION_AND_SUB]
        status = [int(s) for s in status]

        group_ids = await self.fetch(
            "SELECT group_id FROM partner_groups WHERE partner_type = ANY($1)",
            status
        )

        return [group_id[0] for group_id in group_ids]

    async def get_all(self, creator_id: int = None, status: PartnerTypes = PartnerTypes.SUB_GROUPS) -> list[int]:
        if not status:
            status = list(range(0, 4))
        elif not isinstance(status, list):
            status = [status, PartnerTypes.PROMOTION_AND_SUB]
        status = [int(s) for s in status]

        query = "SELECT * FROM partner_groups WHERE partner_type = ANY($1) "
        query_args = [status]
        if creator_id:
            query += "AND creator_id = $2"
            query_args.append(creator_id)

        groups_info = await self.fetch(
            query, *query_args
        )

        return groups_info

    async def get_active_need_group_ids(self, group_id: int):
        need_group_ids = await self.fetchval(
            "SELECT need_groups FROM partner_groups WHERE group_id = $1",
            group_id
        )
        if not need_group_ids:
            return []

        active_need_group_ids = []
        for need_group_id in need_group_ids:
            linked_group_id = await self.fetchval(
                """
                SELECT $1::bigint
                WHERE EXISTS (
                    SELECT 1
                    FROM ad_groups
                    WHERE group_id = $1 AND status = $2
                )
                OR EXISTS (
                    SELECT 1
                    FROM vk_groups
                    WHERE group_id = abs($1)
                );
                """,
                need_group_id, int(AdGroupsStatus.ACTIVE)
            )

            if linked_group_id is not None:
                active_need_group_ids.append(linked_group_id)

        return active_need_group_ids

    async def get_by_poster_id(self, poster_id: int) -> list[Record]:
        groups_info = await self.fetch(
            "SELECT * FROM partner_groups WHERE $1::integer[] && show_ad_ids",
            [poster_id]
        )

        return groups_info

    async def get_by_db_id(self, group_id: int) -> Record:
        group_info = await self.fetchrow(
            "SELECT * FROM partner_groups WHERE id = $1",
            group_id
        )

        return group_info

    async def get_by_group_id(self, group_id: int) -> Record:
        group_info = await self.fetchrow(
            "SELECT * FROM partner_groups WHERE group_id = $1",
            group_id
        )

        return group_info

    async def get_creator_id(self, group_id: int) -> Record:
        creator_id = await self.fetchval(
            "SELECT creator_id FROM partner_groups WHERE group_id = $1",
            group_id
        )

        return creator_id

    async def get_by_ad_group_id(self, ad_group_id: int) -> list[Record]:
        groups_info = await self.fetch(
            "SELECT * FROM partner_groups WHERE $1::bigint[] && need_groups",
            [ad_group_id]
        )

        return groups_info

    async def get_by_poster_info(self, poster_info):
        query = """
            SELECT *
            FROM partner_groups
                WHERE 
                (
                    0 = ANY($1::smallint[])
                    OR 0 = ANY(partner_groups.region_codes)
                    OR partner_groups.region_codes && $1::smallint[]
                )
                AND
                (
                    0 = ANY($2::smallint[])
                    OR 0 = ANY(partner_groups.poster_categories)
                    OR partner_groups.poster_categories && $2::smallint[]
                )
                AND partner_type IN ($3, $4);
        """

        groups_info = await self.fetch(
            query,
            poster_info["region_codes"], [poster_info["topic_id"]], int(PartnerTypes.PROMOTION), int(PartnerTypes.PROMOTION_AND_SUB)
        )

        return groups_info

    async def group_has_sub(self, group_id):
        group = await self.get_by_group_id(group_id)
        if not group:
            return False

        return group["partner_type"] in [PartnerTypes.SUB_GROUPS, PartnerTypes.PROMOTION_AND_SUB]

    async def delete(self, group_db_id: int):
        await self.execute(
            "DELETE FROM partner_groups WHERE id = $1",
            group_db_id
        )


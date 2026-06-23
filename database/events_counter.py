from enum import IntEnum
from typing import Optional, Literal

from asyncpg import Record

from .base import Database


class EventType(IntEnum):
    SUB_BUTTON_PRESSED = 0
    ADDED_NEW_ADVERT = 1
    ADDED_NEW_PARTNER = 2


class EventsCounter(Database):
    def __init__(self):
        super().__init__()

    async def add(self, event_type: EventType):
        await self.connect()

        await self.execute(
            "INSERT INTO events_counter(type) VALUES ($1);",
            int(event_type)
        )

        return self

    async def get_count_by_period(
            self,
            event_type: EventType,
            period: Literal["7 days", "1 month", "1 year", "all_time"]
    ):
        event_count = await self.fetchval(
            """
            SELECT COUNT(*)
            FROM events_counter
            WHERE event_at >= CASE
                WHEN $1 = '7 days' THEN NOW() - INTERVAL '7 days'
                WHEN $1 = '1 month' THEN NOW() - INTERVAL '1 month'
                WHEN $1 = '1 year' THEN NOW() - INTERVAL '1 year'
                ELSE NOW() - INTERVAL '100 years'
            END
            AND type = $2
            """,
            period,
            int(event_type)
        )

        return event_count


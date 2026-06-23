from datetime import datetime
from enum import IntEnum

from asyncpg import Record

from .base import Database


class PosterStatus(IntEnum):
    DELETED = -1
    MODERATED = 0
    ACTIVE = 1
    FROZEN = 2



class Queue(Database):
    def __init__(self):
        super().__init__()

    async def add(self, activate_time: datetime, group_id: int, poster_id: int):
        await self.connect()

        await self.execute(
            "INSERT INTO queue(activate_time, group_id, poster_id) VALUES ($1, $2, $3);",
            activate_time.replace(second=0, microsecond=0), group_id, poster_id
        )

        return self

    async def get_events(self, current_time: datetime = None) -> list[Record]:
        if not current_time:
            current_time = self.now_msk.replace(second=0, microsecond=0)

        events = await self.fetch(
            "SELECT * FROM queue WHERE activate_time = $1;",
            current_time
        )

        return events

    async def get_group_events(self, group_id: int):
        events = await self.fetch(
            "SELECT * FROM queue WHERE group_id = $1 AND activate_time >= $2 AND activate_time < $2 + INTERVAL '1 day';",
            group_id, self.now_msk.date()
        )

        return events

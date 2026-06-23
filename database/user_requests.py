from enum import IntEnum
from typing import Optional

from .base import Database


class RequestType(IntEnum):
    WITHDRAWAL = 1

class RequestStatus(IntEnum):
    CANCELED = -1
    MODERATED = 0
    APPROVED = 2
    REJECTED = 3

class UserRequests(Database):
    def __init__(self):
        super().__init__()

    async def add(self, user_id: int, type: RequestType, status: RequestStatus = RequestStatus.MODERATED, comment: Optional[str] = None, amount: Optional[int] = None):
        await self.connect()

        await self.execute(
            "INSERT INTO user_requests(type, status, user_id, comment, amount) VALUES ($1, $2, $3, $4, $5);",
            int(type), int(status), user_id, comment, amount
        )

    async def change_status(self, request_id: int, status: RequestStatus):
        await self.execute(
            "UPDATE user_requests SET status = $1 WHERE id = $2",
            int(status), request_id
        )

    async def get_requests(self, user_id: int, status: Optional[RequestStatus] = None):
        comp_sign = "="
        if user_id == 0:
            comp_sign = "!="

        if status is not None:
            user_requests = await self.fetch(
                f"SELECT * FROM user_requests WHERE user_id {comp_sign} $1 AND status = $2",
                user_id, status
            )
        else:
            user_requests = await self.fetch(
                f"SELECT * FROM user_requests WHERE user_id {comp_sign} $1",
                user_id
            )

        return user_requests

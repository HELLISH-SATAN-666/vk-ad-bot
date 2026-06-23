import asyncio
from enum import StrEnum
from typing import Optional

from asyncpg import Record
from dotenv import load_dotenv

from .base import Database

class UserStatus(StrEnum):
    # Status P(artner) A(dvertiser) A(dmin) like 000 or 010 or else
    BLOCKED = "---"
    NO_ROLE = "000"
    ADMIN = "001"
    ADVERTISER = "010"
    PARTNER = "100"


class Users(Database):
    def __init__(self):
        super().__init__()

    async def add(self, user_id: int, status: str = UserStatus.NO_ROLE, referral_user_id: Optional[int] = None):
        await self.connect()

        await self.execute(
            "INSERT INTO users(user_id, status, referral_user_id) VALUES ($1, $2, $3);",
            user_id, status, referral_user_id
        )

        return self

    async def update_status(self, user_id: int, status: str):
        old_status: str = await self.fetchval(
            "SELECT status FROM users WHERE user_id = $1;",
            user_id
        )

        if old_status == "---" or old_status[status.index("1")] == "1":
            return

        if status == UserStatus.ADVERTISER:
            # Добавляем новый ивент появления нового рекламодателя
            await self.execute(
                "INSERT INTO events_counter(type) VALUES (1);"
            )

        new_status = "".join([max([old_status_key, status_key], key=lambda v: int(v))
                              for old_status_key, status_key in zip(old_status, status)])

        await self.execute(
            "UPDATE users SET status = $1 WHERE user_id = $2;",
            new_status, user_id
        )

    async def in_db(self, user_id: int) -> bool:
        partner_id = await self.fetchval(
            "SELECT id FROM users WHERE user_id = $1;",
            user_id
        )

        return bool(partner_id)

    async def get_user(self, user_id: int) -> Record:
        user = await self.fetchrow(
            "SELECT * FROM users WHERE user_id = $1;",
            user_id
        )

        return user

    async def get_users_by_status(self, status: str) -> list[Record]:
        statuses_alias = {
            UserStatus.ADMIN: ["3", "1"],
            UserStatus.NO_ROLE: ["3", "2"],
            UserStatus.ADVERTISER: ["2", "1"],
            UserStatus.PARTNER: ["1", "1"]
        }

        users = await self.fetch(
            f"SELECT user_id FROM users WHERE status = $1 OR substring(status, {statuses_alias[status][0]}, 1) = $2;",
            status, statuses_alias[status][1]
        )

        return users

    async def get_user_count(self):
        user_count = await self.fetchval(
            f"SELECT COUNT(*) FROM users;",
        )

        return user_count

    async def get_referrals(self, user_id: int):
        referrals = await self.fetch(
            "SELECT * FROM users WHERE referral_user_id = $1",
            user_id
        )

        return referrals


async def main():
    async with Users() as u:
        print(await u.get_users_by_status(UserStatus.NO_ROLE))

if __name__ == '__main__':
    load_dotenv("../.env")

    asyncio.run(main())
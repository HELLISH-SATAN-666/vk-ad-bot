from asyncpg import Record

from .base import Database

class Partners(Database):
    def __init__(self):
        super().__init__()

    async def add(self, user_id: int):
        await self.connect()

        await self.execute(
            "INSERT INTO partners(user_id) VALUES ($1);",
            user_id
        )

        return self

    async def update_balance(self, user_id: int, cash: int):
        """
        Прибавляет к балансу сумму *cash*
        :param user_id: Id пользователя, на баланс которого придут деньги
        :param cash: Сумма денег
        """

        await self.execute(
            "UPDATE partners SET balance = balance + $1 WHERE user_id = $2;",
            cash, user_id
        )

    async def in_db(self, user_id: int) -> bool:
        partner_id = await self.fetchval(
            "SELECT id FROM partners WHERE user_id = $1;",
            user_id
        )

        return bool(partner_id)

    async def get_user(self, user_id: int) -> Record:
        partner_data = await self.fetchrow(
            "SELECT * FROM partners WHERE user_id = $1;",
            user_id
        )

        return partner_data

    async def get_all(self) -> list[Record]:
        partners_data = await self.fetch(
            "SELECT * FROM partners;",
        )

        return partners_data

    async def get_user_groups(self, user_id: int) -> list[Record]:
        partner_groups_data = await self.fetch(
            "SELECT pg.id as pg_id, pg.partner_type as pg_status, p.user_id as user_id, p.balance as balance, pg.group_id as group_id "
            "FROM partners p LEFT JOIN partner_groups pg ON p.user_id = pg.creator_id "
            "WHERE p.user_id = $1 "
            "ORDER BY pg_id;",
            user_id
        )

        return partner_groups_data

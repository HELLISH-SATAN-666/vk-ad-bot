from enum import IntEnum
from typing import Optional, Literal

from asyncpg import Record

from .base import Database


class PaymentTypes(IntEnum):
    AD_GROUP = 0
    NEWSLETTER = 1
    POSTER = 2


class Payments(Database):
    def __init__(self):
        super().__init__()

    async def add(self, from_user: int, to_user: int, pay_sum: int, pay_type: PaymentTypes):
        """
        Добавляет новый платеж для составления статистики.
        :param from_user: Пользователь отправляющий деньги
        :param to_user: Пользователь получивший деньги или 0 если это вывод
        :param pay_sum: Сумма платёжа
        :param pay_type: Тип платежа для дальнейшей сортировки
        """

        await self.connect()

        await self.execute(
            "INSERT INTO payments(type, from_user, to_user, sum) VALUES ($1, $2, $3, $4);",
            int(pay_type), from_user, to_user, pay_sum
        )

        return self

    async def get_all(self, from_user: Optional[int] = None, to_user: Optional[int] = None, pay_type: Optional[PaymentTypes] = None):
        query = "SELECT * FROM payments "
        query_args: list = []

        add_queries: list[str] = []


        for db_var, var in {"from_user": from_user, "to_user": to_user, "type": pay_type}.items():
            if var:
                add_queries.append(db_var + " = {}")
                query_args.append(var)

        if len(query_args) > 0:
            add_queries = [q.format("$" + str(i + 1)) for i, q in enumerate(add_queries)]
            query += f"WHERE {' AND '.join(add_queries)}"

        pays = await self.fetch(
            query,
            *query_args
        )

        return pays

    async def get_by_period(self, period: Literal["7 days", "1 month", "1 year", "all_time"]):
        sum_pays = await self.fetchval(
            """
            SELECT SUM(sum)
            FROM payments
            WHERE created_at >= CASE
                WHEN $1 = '7 days' THEN NOW() - INTERVAL '7 days'
                WHEN $1 = '1 month' THEN NOW() - INTERVAL '1 month'
                WHEN $1 = '1 year' THEN NOW() - INTERVAL '1 year'
                ELSE NOW() - INTERVAL '100 years'
            END
            """,
            period
        )

        return sum_pays

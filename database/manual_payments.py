from json import dumps, loads

from .base import Database

class ManualPayments(Database):
    def __init__(self):
        super().__init__()

    async def add(self, payment_state: dict):
        await self.connect()

        pay_id = await self.fetchval(
            "INSERT INTO manual_payments(payment_state) VALUES ($1)"
            "RETURNING id;",
            dumps(payment_state, ensure_ascii=False)
        )

        return pay_id

    async def get_payment_state(self, pay_id: int) -> dict:
        payment_state = await self.fetchval(
            f"SELECT payment_state FROM manual_payments WHERE id = $1;",
            pay_id
        )
        return payment_state if isinstance(payment_state, dict) else loads(payment_state)

    async def get_all(self):
        return await self.fetch("SELECT * FROM manual_payments ORDER BY id;")

    async def delete(self, pay_id: int):
        await self.execute(
            "DELETE FROM manual_payments WHERE id = $1;",
            pay_id
        )

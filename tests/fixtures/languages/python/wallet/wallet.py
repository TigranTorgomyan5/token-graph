from wallet.base import BaseWallet
from wallet.ledger import record


class Wallet(BaseWallet):
    def __init__(self) -> None:
        self._amount = 12

    def balance(self) -> int:
        return self._amount

    def debit(self, amount: int) -> None:
        if self.balance() < amount:
            raise ValueError("Insufficient funds")
        self._amount -= amount
        record(f"debit {amount}")


class BonusWallet(Wallet):
    bonus = 5

    def balance(self) -> int:
        return super().balance() + self.bonus

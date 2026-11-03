from abc import ABC, abstractmethod


class BaseWallet(ABC):
    @abstractmethod
    def balance(self) -> int: ...

    @abstractmethod
    def debit(self, amount: int) -> None: ...

"""Conformance corpus: every Python construct the spec claims to extract."""
import os
from typing import Protocol

MAX_RETRIES = 3
_state = {}


class Base:
    """A base class."""
    shared: int = 0

    def shared_method(self) -> int:
        return self.shared


class Handler(Protocol):
    def handle(self, value: int) -> bool: ...


class Order(Base):
    """An order."""
    total: float = 0.0
    currency = "USD"

    def __init__(self, items: list, tax: float = 0.0):
        self.items = items
        self.tax = tax

    async def submit(self, repo: "Repository") -> bool:
        """Submit it."""
        amount = self.total + self.tax
        ok = repo.save(amount, self.currency)
        for item in self.items:
            repo.log(item)
        return ok

    def shared_method(self) -> int:
        return 1


class Repository:
    def save(self, amount, currency):
        self.last = amount
        return self.last

    def log(self, item):
        raise ValueError(item)


@staticmethod
def decorated(x: int) -> int:
    y = x * 2
    return y


def uses_env():
    return os.environ.get("SHOP_TOKEN")

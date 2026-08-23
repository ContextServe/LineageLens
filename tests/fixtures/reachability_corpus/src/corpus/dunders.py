"""Dunder methods the language invokes implicitly; no call site ever names them."""


class Ctx:
    def __enter__(self) -> "Ctx":
        """Rescue: implicit_dunder."""
        return self

    def __exit__(self, *exc: object) -> bool:
        """Rescue: implicit_dunder."""
        return False


class Model:
    def __init__(self, value: int) -> None:
        """Rescue: implicit_dunder. `Model()` resolves to the class, never here."""
        self.value = value


class Proxy:
    def __getattr__(self, name: str) -> None:
        """Rescue: implicit_dunder."""
        return None


def use_them() -> int:
    proxy = Proxy()
    with Ctx():
        return Model(1).value + (1 if proxy.anything is None else 0)

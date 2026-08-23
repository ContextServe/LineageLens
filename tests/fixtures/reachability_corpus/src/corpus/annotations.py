"""Classes referenced only from type annotations."""

from typing import Optional


class Payload:
    """Referenced only as a parameter annotation. Rescue: type_annotation."""


class Result:
    """Referenced only as a return annotation. Rescue: type_annotation."""


class Nested:
    """Referenced only inside a subscript annotation. Rescue: type_annotation."""


class Later:
    """Referenced only from a string forward reference. Rescue: type_annotation."""


def takes_payload(item: Payload) -> Result:
    return Result()


def takes_nested(items: Optional[list[Nested]]) -> None:
    return None


def returns_later() -> "Later":
    raise NotImplementedError

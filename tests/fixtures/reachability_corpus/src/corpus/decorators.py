"""A locally-defined decorator is referenced only by the @ syntax."""

from collections.abc import Callable


def memoize(fn: Callable[[], int]) -> Callable[[], int]:
    """Referenced only as a decorator. Rescue: decorator."""
    cache: dict[str, int] = {}

    def wrapper() -> int:
        if "v" not in cache:
            cache["v"] = fn()
        return cache["v"]

    return wrapper


@memoize
def cached_thing() -> int:
    return 42

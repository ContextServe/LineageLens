"""The escape hatch of last resort.

Some code is genuinely only reachable in ways static analysis cannot see -- a
plugin loaded from a config file, a handler named in an environment variable.
The pragma is how a maintainer says so, on the record, in the source.
"""


def kept_by_pragma() -> int:  # lineagelens: keep
    """Nothing references this. The pragma is the only thing keeping it alive."""
    return 1


def kept_by_pragma_on_decorator() -> int:
    """Control: same shape, no pragma. Must stay dead."""
    return 2

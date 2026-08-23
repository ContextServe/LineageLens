"""Functions referenced as values, never called by name.

This is the largest false-positive bucket in real code: a bare `ast.Name` load
inside a dict or list literal produces no CALLS edge, so the handler looks
uncalled. Both references here are at *module scope*, which additionally
requires the module-scope source node to exist.
"""


def handler_a() -> str:
    """Referenced as a dict value at module scope. Rescue: passed_as_value."""
    return "a"


def handler_b() -> str:
    """Referenced as a list element at module scope. Rescue: passed_as_value."""
    return "b"


HANDLERS = {"a": handler_a}
DISPATCH = [handler_b]

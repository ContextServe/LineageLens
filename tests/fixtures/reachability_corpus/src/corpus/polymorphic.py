from .inheritance import Base, ChildA


def consume(provider: Base) -> int:
    """Calls op() through the annotated base type, not a concrete class."""
    return provider.op()


def make() -> int:
    return consume(ChildA())

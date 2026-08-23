from abc import ABC, abstractmethod


class Base(ABC):
    """Referenced only as a base class. Rescue: base_class."""

    @abstractmethod
    def op(self) -> int:
        """Abstract declaration, overridden below. Rescue: abstract_declaration."""


class ChildA(Base):
    def op(self) -> int:
        """Override of Base.op. Rescue: polymorphic_override."""
        return 1


class ChildB(Base):
    def op(self) -> int:
        """Override of Base.op. Rescue: polymorphic_override."""
        return 2

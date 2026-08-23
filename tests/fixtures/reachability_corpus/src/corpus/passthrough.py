"""Annotated-parameter passthrough: the most common attribute binding shape."""

from .shadow_b import Engine


class Service:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def go(self) -> int:
        return self.engine.run()

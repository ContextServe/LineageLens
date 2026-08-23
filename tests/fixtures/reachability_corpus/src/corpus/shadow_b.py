class Engine:
    """Same name as shadow_a.Engine. Only this one is constructed below."""

    def run(self) -> int:
        return 2


def drive() -> int:
    engine = Engine()
    return engine.run()

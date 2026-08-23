"""FastAPI-shaped route, dependency injection, and lifespan callback."""


class _Router:
    def get(self, path: str):
        def decorate(fn):
            return fn

        return decorate


router = _Router()


def Depends(dep):  # noqa: N802 - mirrors the FastAPI name deliberately
    return dep


def dep_fn() -> int:
    """Referenced only inside Depends(...). Rescue: passed_as_value."""
    return 1


def lifespan_fn() -> None:
    """Referenced only as a keyword argument. Rescue: passed_as_value."""
    return None


class _App:
    def __init__(self, lifespan=None) -> None:
        self.lifespan = lifespan


app = _App(lifespan=lifespan_fn)


@router.get("/items")
def list_items(dep: int = Depends(dep_fn)) -> int:
    """Rescue: entry_point:api_route."""
    return dep

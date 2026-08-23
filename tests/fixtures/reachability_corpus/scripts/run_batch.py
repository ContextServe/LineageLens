"""Module-scope wiring only.

Every call here is at module level. While relations required an enclosing
function, this file produced no edges at all -- which is exactly how framework
wiring (`app = FastAPI()`, `include_router(...)`, registry population) became
invisible to the graph.

Deliberately does *not* call corpus.console.main: that would give `main` a
static caller and mask the console-script entry-point rule it exists to test.
"""


def build() -> int:
    return 1


def wire(value: int) -> int:
    return value + 1


app = build()
wired = wire(app)

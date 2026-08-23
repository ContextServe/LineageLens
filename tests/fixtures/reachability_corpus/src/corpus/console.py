"""The console-script entry point, and the hub that drives every rescue chain.

`[project.scripts] corpus-cli = "corpus.console:main"` makes `main` a
reachability root. Everything reachable from here must end up alive, by a
*named* mechanism -- that is what the golden table asserts.
"""

from .annotations import takes_nested, takes_payload, returns_later
from .decorators import cached_thing
from .dunders import use_them
from .inheritance import ChildA, ChildB
from .passthrough import Service
from .polymorphic import consume
from .registry import DISPATCH, HANDLERS
from .shadow_b import Engine, drive
from .tested_only import shared_by_test_and_main
from .src.nested import nested_fn


def main() -> int:
    """Target of [project.scripts]. Rescue: entry_point:console_script."""
    total = helper_called_by_main()
    total += use_them()
    total += consume(ChildA())
    total += consume(ChildB())
    total += drive()
    total += Service(Engine()).go()
    total += cached_thing()
    total += nested_fn()
    total += shared_by_test_and_main()
    total += len(HANDLERS) + len(DISPATCH)
    takes_payload(make_payload())
    takes_nested(None)
    try:
        returns_later()
    except NotImplementedError:
        pass
    return total


def helper_called_by_main() -> int:
    """Reached from main() by a plain call. Rescue: static_call."""
    return 0


def make_payload():
    """Constructs the annotation-only class so it also has a call edge."""
    from .annotations import Payload

    return Payload()

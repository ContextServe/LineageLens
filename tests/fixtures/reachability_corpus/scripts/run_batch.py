"""Module-scope wiring only. Every call here is at module level, which produced
no relation at all while relations required an enclosing function."""

from corpus.console import main


def build() -> int:
    return 1


app = build()
result = main()

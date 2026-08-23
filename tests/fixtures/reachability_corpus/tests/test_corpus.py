from corpus.tested_only import only_tests_call_me, shared_by_test_and_main
from helpers import helper


def test_x(db: str) -> None:
    """Entry point: test. Consumes the `db` fixture by name, not by reference."""
    assert helper() == 1
    assert db == "db"
    assert only_tests_call_me() == 7
    assert shared_by_test_and_main() == 8

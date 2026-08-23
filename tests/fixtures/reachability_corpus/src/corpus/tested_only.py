"""Source-scope functions with different reachability provenance.

`only_tests_call_me` is production code whose sole caller is a test -- the
`test_only` verdict. That is materially different from both "alive" and "dead":
deleting it breaks the suite, but nothing ships that uses it.
"""


def only_tests_call_me() -> int:
    """Reached solely from a test root. Verdict: test_only."""
    return 7


def shared_by_test_and_main() -> int:
    """Reached from both a test and the console entry point. Verdict: alive."""
    return 8

def helper() -> int:
    """Called from a test. Rescue: static_call, scope: test."""
    return 1


def unused_helper() -> int:
    """Called by nothing, in a test file. Verdict: dead, scope: test."""
    return 2

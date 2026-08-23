"""Nothing in the corpus references anything in this module, by any mechanism.

These are the control group. If a change to reachability rescues them, the rules
have become too permissive.
"""


def never_used() -> int:
    return 1


class NeverUsed:
    def also_never_used(self) -> int:
        return 2

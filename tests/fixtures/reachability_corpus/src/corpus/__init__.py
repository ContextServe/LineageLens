"""Package init that re-exports a symbol defined elsewhere.

`__all__` is the only thing referencing `exported_only`, so it is the sole
rescue mechanism for that symbol.
"""

from .exported import exported_only

__all__ = ["exported_only"]

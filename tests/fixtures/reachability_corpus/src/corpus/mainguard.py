def run_it() -> int:
    """Called only from a __main__ guard at module scope. Rescue: static_call."""
    return 1


if __name__ == "__main__":
    run_it()

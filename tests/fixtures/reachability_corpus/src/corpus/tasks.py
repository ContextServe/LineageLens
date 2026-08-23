"""Celery-style task registration by decorator."""


class _App:
    def task(self, fn):
        return fn


celery_app = _App()


@celery_app.task
def do_work() -> int:
    """Registered as a task; nothing calls it in-repo. Rescue: entry_point:task."""
    return 1

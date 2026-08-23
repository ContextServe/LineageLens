import pytest


@pytest.fixture
def db() -> str:
    """Injected by name into test_x. Rescue: pytest_fixture_name."""
    return "db"

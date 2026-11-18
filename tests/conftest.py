import os
from collections.abc import Generator
from typing import Any

import psycopg
import pytest
from hypothesis import HealthCheck, settings

from engine import db

settings.register_profile(
    "ci",
    derandomize=True,
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgres://app:app_secret@localhost:5432/macy?sslmode=disable"
)


@pytest.fixture
def db_conn() -> Generator[psycopg.Connection[Any], None, None]:
    conn = db.get_connection(TEST_DATABASE_URL)
    yield conn
    conn.close()

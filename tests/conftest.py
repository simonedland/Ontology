"""Database tests run against a throw-away database `tgstore_test` on the server in TGSTORE_DATABASE_URL
(default: the compose `db`). They are skipped when no PostgreSQL is reachable."""

import os

import psycopg
import pytest

ADMIN_URL = os.environ.get("TGSTORE_DATABASE_URL", "postgresql://tgstore:tgstore@127.0.0.1:5432/tgstore")
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/tgstore_test"


@pytest.fixture(scope="session")
def database_url():
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
            conn.execute("DROP DATABASE IF EXISTS tgstore_test WITH (FORCE)")
            conn.execute("CREATE DATABASE tgstore_test")
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    return TEST_URL


@pytest.fixture
def store(database_url):
    from tgstore.db import Store

    s = Store(database_url, pool_size=4)
    with s.pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS chats, users, user_history, messages, memberships")
    s.migrate()
    yield s
    s.close()

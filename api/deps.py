"""Request-scoped dependencies.

A fresh SQLite connection per request keeps things thread-safe under FastAPI's
sync-endpoint threadpool (SQLite connections must not be shared across threads),
and it is cheap: the file is already warm in the OS page cache.  The connection
is read-only in practice -- no handler issues a write.
"""
from __future__ import annotations

from typing import Iterator

from data.db import get_conn


def db() -> Iterator:
    """Yield a per-request DB connection, closed when the request ends.

    check_same_thread=False because FastAPI may run this dependency's setup
    and teardown on different threadpool threads; the connection is still
    request-scoped and never used by two threads at once.
    """
    conn = get_conn(check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

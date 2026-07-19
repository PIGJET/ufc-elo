"""Shared SQLite access for the UFC Elo project.

Every module (ingestion, scrapers, elo engine, API) goes through get_conn()
so the schema is applied uniformly and a future Postgres swap touches one file.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
DB_PATH = DATA_DIR / "ufc.db"
SCHEMA_PATH = DATA_DIR / "schema.sql"


def get_conn(db_path: Path | str = DB_PATH, *,
             check_same_thread: bool = True) -> sqlite3.Connection:
    # check_same_thread=False is for callers that hand a connection between
    # threads sequentially (e.g. FastAPI yield-dependencies, whose setup and
    # teardown may run on different threadpool threads). Never share one
    # connection between threads concurrently.
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Create/upgrade the schema (idempotent) and return a connection."""
    conn = get_conn(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def log_provenance(conn: sqlite3.Connection, entity: str, entity_id: int,
                   field: str, source: str, value_seen) -> None:
    conn.execute(
        "INSERT INTO provenance (entity, entity_id, field, source, value_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (entity, entity_id, field, source,
         None if value_seen is None else str(value_seen)),
    )


if __name__ == "__main__":
    init_db()
    print(f"Schema applied to {DB_PATH}")

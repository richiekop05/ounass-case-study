"""One database connection for the app, shared by requests and the job runner.

The app is local with one reviewer, so a single connection guarded by a lock (the pipeline's `Db`
wrapper) is enough. It reconnects once if the connection has dropped.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from scraper.store import normalize_db_url
from tagger.runs import Db

ROOT = Path(__file__).resolve().parents[2]
_lock = threading.Lock()
_db: Db | None = None


class ConfigError(RuntimeError):
    pass


def _connect() -> Db:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        raise ConfigError("SUPABASE_DB_URL is not set in .env")
    return Db(psycopg.connect(normalize_db_url(url), autocommit=False))


def get_db() -> Db:
    """The shared connection, reconnected if it was closed or broken."""
    global _db
    with _lock:
        if _db is None or _db.conn.closed or _db.conn.broken:
            _db = _connect()
        return _db


def end(db: Db) -> None:
    """End a read: roll back so later reads see fresh data. In tests (commit=False) do nothing, so the
    test's single transaction survives."""
    if db._commit:
        db.conn.rollback()


def commit(db: Db) -> None:
    db.commit()


def read(sql: str, params=()) -> list[tuple]:
    """Run a read-only query and end its transaction, so later reads see fresh data."""
    db = get_db()
    with db.lock:
        try:
            rows = db.conn.execute(sql, params).fetchall()
        finally:
            end(db)
    return rows

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.settings import settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path or settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(path: Path | None = None) -> None:
    schema = (Path(__file__).resolve().parent / "sql" / "schema.sqlite.sql").read_text(encoding="utf-8")
    conn = connect(path)
    try:
        conn.executescript(schema)
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    return row_to_dict(conn.execute(sql, params).fetchone())


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def update(conn: sqlite3.Connection, table: str, row_id: str, tenant_id: str, values: dict[str, Any]) -> None:
    if not values:
        return
    assignments = ", ".join(f"{k} = ?" for k in values)
    conn.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ? AND tenant_id = ?",
        (*values.values(), row_id, tenant_id),
    )

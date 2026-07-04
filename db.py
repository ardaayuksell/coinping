"""SQLite storage for alarms. WAL mode for safe concurrent reads/writes."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "coinping.db"
_conn: sqlite3.Connection | None = None


def _get() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init() -> None:
    _get().execute(
        """
        CREATE TABLE IF NOT EXISTS alarms (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            symbol     TEXT    NOT NULL,
            direction  TEXT    NOT NULL,   -- '>' or '<'
            target     REAL    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    _get().commit()


def add_alarm(chat_id: int, symbol: str, direction: str, target: float) -> int:
    cur = _get().execute(
        "INSERT INTO alarms (chat_id, symbol, direction, target) VALUES (?, ?, ?, ?)",
        (chat_id, symbol, direction, target),
    )
    _get().commit()
    return cur.lastrowid


def all_alarms() -> list[dict]:
    rows = _get().execute("SELECT * FROM alarms").fetchall()
    return [dict(r) for r in rows]


def list_alarms(chat_id: int) -> list[dict]:
    rows = _get().execute(
        "SELECT * FROM alarms WHERE chat_id = ? ORDER BY id", (chat_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_alarm(alarm_id: int, chat_id: int) -> int:
    cur = _get().execute(
        "DELETE FROM alarms WHERE id = ? AND chat_id = ?", (alarm_id, chat_id)
    )
    _get().commit()
    return cur.rowcount

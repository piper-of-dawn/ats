"""Transactional, compressed feed checkpoints for one collection run."""

import json
import sqlite3
import zlib
from pathlib import Path

from .massive import base_url


class Checkpoints:
    """A local SQLite file; each saved feed is committed independently."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.connection = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        try:
            self.connection.execute("PRAGMA synchronous=FULL")
            with self.connection:
                self.connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.connection.execute("""CREATE TABLE IF NOT EXISTS feeds (
                    ticker TEXT NOT NULL, dataset TEXT NOT NULL, status TEXT NOT NULL,
                    payload BLOB NOT NULL, PRIMARY KEY (ticker, dataset)
                )""")
                origin = base_url()
                for key, value in (("format_version", "1"), ("massive_origin", origin)):
                    existing = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
                    if existing and existing[0] != value:
                        raise ValueError("Checkpoint settings differ; choose a new checkpoint file")
                    self.connection.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
        except BaseException:
            self.connection.close()
            raise
        return self

    def __exit__(self, *exc):
        self.connection.close()

    def window(self) -> tuple[str, str] | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = 'window'").fetchone()
        return tuple(json.loads(row[0])) if row else None

    def bind_window(self, start: str, end: str) -> None:
        with self.connection:
            saved = self.window()
            if saved and saved != (start, end):
                raise ValueError("Checkpoint date window differs; choose a new checkpoint file for a new collection")
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('window', ?)", (json.dumps([start, end]),))

    def load(self, ticker: str, dataset: str) -> dict | None:
        row = self.connection.execute(
            "SELECT payload FROM feeds WHERE ticker = ? AND dataset = ? AND status IN ('ok', 'empty')",
            (ticker, dataset),
        ).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def save(self, ticker: str, dataset: str, entry: dict) -> None:
        payload = zlib.compress(json.dumps(entry, allow_nan=False).encode("utf-8"))
        with self.connection:
            self.connection.execute("""INSERT INTO feeds VALUES (?, ?, ?, ?)
                ON CONFLICT (ticker, dataset) DO UPDATE SET status=excluded.status, payload=excluded.payload""",
                (ticker, dataset, entry["status"], payload),
            )

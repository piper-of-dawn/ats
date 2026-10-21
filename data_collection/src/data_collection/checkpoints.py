"""Transactional, compressed feed checkpoints for one collection run."""

import json
import sqlite3
import zlib
from pathlib import Path

from .massive import base_url


class Checkpoints:
    """A local SQLite file; each saved feed is committed independently."""

    def __init__(self, path: str | Path, *, provider="massive"):
        self.path = Path(path)
        self.provider = provider
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
                self.connection.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, payload BLOB NOT NULL)")
                # Older checkpoints without an explicit provider belong to Massive.
                prior = self.connection.execute("SELECT value FROM metadata WHERE key = 'provider'").fetchone()
                legacy = self.connection.execute("SELECT value FROM metadata WHERE key = 'massive_origin'").fetchone()
                if (prior and prior[0] != self.provider) or (not prior and legacy and self.provider != "massive"):
                    raise ValueError("Checkpoint provider differs; choose a new checkpoint file")
                metadata = [("format_version", "2"), ("provider", self.provider)]
                if self.provider == "massive":
                    metadata.append(("massive_origin", base_url()))
                for key, value in metadata:
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

    def bind_options(self, settings: dict) -> None:
        value = json.dumps(settings, sort_keys=True)
        row = self.connection.execute("SELECT value FROM metadata WHERE key = 'option_settings'").fetchone()
        if row and row[0] != value:
            raise ValueError("Option settings differ; choose a new checkpoint file")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('option_settings', ?)", (value,))

    def bind_run(self, settings: dict) -> None:
        """Bind provider, universe, price basis and model before exporting a run."""
        value = json.dumps(settings, sort_keys=True)
        row = self.connection.execute("SELECT value FROM metadata WHERE key = 'run_settings'").fetchone()
        if row and row[0] != value:
            raise ValueError("Run settings differ; choose a new checkpoint file")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('run_settings', ?)", (value,))

    def response(self, key: str):
        row = self.connection.execute("SELECT payload FROM responses WHERE key = ?", (key,)).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def save_response(self, key: str, payload) -> None:
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)",
                                    (key, zlib.compress(json.dumps(payload, allow_nan=False).encode())))

    def save(self, ticker: str, dataset: str, entry: dict) -> None:
        payload = zlib.compress(json.dumps(entry, allow_nan=False).encode("utf-8"))
        with self.connection:
            self.connection.execute("""INSERT INTO feeds VALUES (?, ?, ?, ?)
                ON CONFLICT (ticker, dataset) DO UPDATE SET status=excluded.status, payload=excluded.payload""",
                (ticker, dataset, entry["status"], payload),
            )

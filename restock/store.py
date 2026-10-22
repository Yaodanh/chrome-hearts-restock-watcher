"""Remembers the last known stock status of each product (SQLite)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from .models import StockStatus


class StateStore:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS product_state (
                   product_id TEXT PRIMARY KEY,
                   status     TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        self.conn.commit()

    def load(self) -> Dict[str, StockStatus]:
        rows = self.conn.execute("SELECT product_id, status FROM product_state").fetchall()
        return {pid: StockStatus(status) for pid, status in rows}

    def save(self, product_id: str, status: StockStatus) -> None:
        if status is StockStatus.UNKNOWN:
            return  # never overwrite a real observation with "don't know"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO product_state (product_id, status, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(product_id) DO UPDATE SET status=excluded.status,
                                                     updated_at=excluded.updated_at""",
            (product_id, status.value, now),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

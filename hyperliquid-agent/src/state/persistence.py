"""SQLite crash recovery — persist agent state across restarts."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.state.portfolio import EquitySnapshot, Fill

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "nxfh01.db"


class StateStore:
    """SQLite-backed persistence for crash recovery."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        logger.info("StateStore opened: %s", self._db_path)

    def _migrate(self) -> None:
        cur = self._conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                strategy TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                equity REAL NOT NULL,
                return_pct REAL NOT NULL DEFAULT 0.0
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def save_fill(self, fill: Fill) -> None:
        try:
            self._conn.execute(
                "INSERT INTO fills (coin, side, size, entry_price, exit_price, "
                "realized_pnl, entry_time, exit_time, strategy) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fill.coin, fill.side, fill.size, fill.entry_price, fill.exit_price,
                 fill.realized_pnl, fill.entry_time.isoformat(), fill.exit_time.isoformat(),
                 fill.strategy),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error("Failed to save fill: %s", e)

    def load_fills(self) -> list[Fill]:
        try:
            cur = self._conn.execute(
                "SELECT coin, side, size, entry_price, exit_price, realized_pnl, "
                "entry_time, exit_time, strategy FROM fills ORDER BY id"
            )
            fills = []
            for row in cur:
                fills.append(Fill(
                    coin=row[0], side=row[1], size=row[2],
                    entry_price=row[3], exit_price=row[4], realized_pnl=row[5],
                    entry_time=datetime.fromisoformat(row[6]),
                    exit_time=datetime.fromisoformat(row[7]),
                    strategy=row[8],
                ))
            return fills
        except sqlite3.Error as e:
            logger.error("Failed to load fills: %s", e)
            return []

    def save_equity(self, snapshot: EquitySnapshot) -> None:
        try:
            self._conn.execute(
                "INSERT INTO equity_curve (timestamp, equity, return_pct) VALUES (?, ?, ?)",
                (snapshot.timestamp.isoformat(), snapshot.equity, snapshot.return_pct),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error("Failed to save equity snapshot: %s", e)

    def load_equity_curve(self) -> list[EquitySnapshot]:
        try:
            cur = self._conn.execute(
                "SELECT timestamp, equity, return_pct FROM equity_curve ORDER BY id"
            )
            return [
                EquitySnapshot(
                    timestamp=datetime.fromisoformat(row[0]),
                    equity=row[1],
                    return_pct=row[2],
                )
                for row in cur
            ]
        except sqlite3.Error as e:
            logger.error("Failed to load equity curve: %s", e)
            return []

    def set_kv(self, key: str, value: str) -> None:
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error("Failed to set kv %s: %s", key, e)

    def get_kv(self, key: str, default: str = "") -> str:
        try:
            cur = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else default
        except sqlite3.Error as e:
            logger.error("Failed to get kv %s: %s", key, e)
            return default

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

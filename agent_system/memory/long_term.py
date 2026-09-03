"""Long-term memory — persistent store of trade outcomes and lessons.

Uses SQLite for persistence (no external dependencies required).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


class LongTermMemory:
    """Persistent memory for trade outcomes and lessons learned."""

    def __init__(self, settings: Settings, db_path: Optional[str] = None) -> None:
        self._settings = settings
        self._db_path = db_path or os.path.join(
            getattr(settings, 'memory_dir', '.memory'), 'long_term.db'
        )
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    strategy TEXT,
                    symbol TEXT,
                    venues TEXT,
                    expected_edge_bps REAL,
                    actual_pnl REAL,
                    regime TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    category TEXT,
                    content TEXT,
                    importance REAL DEFAULT 1.0
                )
            """)
            conn.commit()

    def record_trade(self, trade_data: Dict[str, Any]) -> None:
        """Record a completed trade."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO trades (timestamp, strategy, symbol, venues, expected_edge_bps, actual_pnl, regime, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    trade_data.get("strategy", "unknown"),
                    trade_data.get("symbol", "unknown"),
                    json.dumps(trade_data.get("venues", [])),
                    trade_data.get("expected_edge_bps", 0),
                    trade_data.get("actual_pnl", 0),
                    trade_data.get("regime", "unknown"),
                    json.dumps(trade_data.get("metadata", {})),
                ),
            )
            conn.commit()

    def record_lesson(self, category: str, content: str, importance: float = 1.0) -> None:
        """Record a lesson learned."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO lessons (timestamp, category, content, importance)
                   VALUES (?, ?, ?, ?)""",
                (time.time(), category, content, importance),
            )
            conn.commit()

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_lessons(self, category: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get lessons, optionally filtered by category."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if category:
                rows = conn.execute(
                    "SELECT * FROM lessons WHERE category = ? ORDER BY importance DESC, timestamp DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM lessons ORDER BY importance DESC, timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get summary statistics of past trades."""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) as total, SUM(actual_pnl) as total_pnl, AVG(actual_pnl) as avg_pnl FROM trades"
            ).fetchone()
            wins = conn.execute("SELECT COUNT(*) FROM trades WHERE actual_pnl > 0").fetchone()[0]
            total = row[0] if row[0] else 0

            return {
                "total_trades": total,
                "total_pnl": row[1] if row[1] else 0,
                "avg_pnl": row[2] if row[2] else 0,
                "win_rate": (wins / total * 100) if total > 0 else 0,
            }
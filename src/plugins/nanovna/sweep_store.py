"""
NanoVNA Sweep Storage - SQLite persistence for sweep history.

Stores frequency sweep results for historical tracking, comparison,
and export. Database lives at ~/.config/meshforge/nanovna_sweeps.db.
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

from .nanovna_device import SweepPoint, SweepResult

logger = logging.getLogger(__name__)


@dataclass
class StoredSweep:
    """A stored frequency sweep result."""
    id: int
    timestamp: float
    start_hz: int
    stop_hz: int
    num_points: int
    min_swr: float
    min_swr_freq_hz: int
    device_info: str
    label: str = ""

    @property
    def start_mhz(self) -> float:
        return self.start_hz / 1e6

    @property
    def stop_mhz(self) -> float:
        return self.stop_hz / 1e6

    @property
    def min_swr_freq_mhz(self) -> float:
        return self.min_swr_freq_hz / 1e6


class SweepStore:
    """SQLite-backed sweep history storage.

    Usage:
        store = SweepStore()
        sweep_id = store.save_sweep(result, label="70cm Yagi v2")
        sweeps = store.list_sweeps()
        points = store.get_sweep_points(sweep_id)
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize sweep store.

        Args:
            db_path: Path to SQLite database. If None, uses default
                     ~/.config/meshforge/nanovna_sweeps.db
        """
        if db_path is None:
            db_path = self._default_db_path()

        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _default_db_path() -> Path:
        """Get default database path using MF001-compliant home detection."""
        from utils.paths import get_real_user_home
        return get_real_user_home() / ".config" / "meshforge" / "nanovna_sweeps.db"

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sweeps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    start_hz INTEGER NOT NULL,
                    stop_hz INTEGER NOT NULL,
                    num_points INTEGER NOT NULL,
                    min_swr REAL,
                    min_swr_freq_hz INTEGER,
                    device_info TEXT DEFAULT '',
                    label TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sweep_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sweep_id INTEGER NOT NULL REFERENCES sweeps(id) ON DELETE CASCADE,
                    frequency_hz INTEGER NOT NULL,
                    s11_real REAL NOT NULL,
                    s11_imag REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sweep_points_sweep_id
                ON sweep_points(sweep_id)
            """)
            conn.commit()

    def save_sweep(self, result: SweepResult, label: str = "") -> int:
        """Store a sweep result.

        Args:
            result: SweepResult to store.
            label: User-assigned label for identification.

        Returns:
            Sweep ID in the database.
        """
        if not result.points:
            raise ValueError("Cannot save empty sweep result")

        min_swr_val, min_swr_freq = result.min_swr
        start_hz = result.points[0].frequency_hz
        stop_hz = result.points[-1].frequency_hz

        with sqlite3.connect(str(self._db_path)) as conn:
            cursor = conn.execute(
                """INSERT INTO sweeps
                   (timestamp, start_hz, stop_hz, num_points, min_swr,
                    min_swr_freq_hz, device_info, label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.timestamp,
                    start_hz,
                    stop_hz,
                    len(result.points),
                    min_swr_val,
                    int(min_swr_freq * 1e6),
                    result.device_info,
                    label,
                ),
            )
            sweep_id = cursor.lastrowid

            point_rows = [
                (sweep_id, p.frequency_hz, p.s11_real, p.s11_imag)
                for p in result.points
            ]
            conn.executemany(
                """INSERT INTO sweep_points
                   (sweep_id, frequency_hz, s11_real, s11_imag)
                   VALUES (?, ?, ?, ?)""",
                point_rows,
            )
            conn.commit()

        logger.info(f"[NanoVNA] Saved sweep {sweep_id}: {len(result.points)} points, label='{label}'")
        return sweep_id

    def get_sweep(self, sweep_id: int) -> Optional[StoredSweep]:
        """Get sweep metadata by ID."""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sweeps WHERE id = ?", (sweep_id,)
            ).fetchone()

            if not row:
                return None

            return StoredSweep(
                id=row["id"],
                timestamp=row["timestamp"],
                start_hz=row["start_hz"],
                stop_hz=row["stop_hz"],
                num_points=row["num_points"],
                min_swr=row["min_swr"],
                min_swr_freq_hz=row["min_swr_freq_hz"],
                device_info=row["device_info"],
                label=row["label"],
            )

    def get_sweep_points(self, sweep_id: int) -> List[SweepPoint]:
        """Get all measurement points for a sweep."""
        with sqlite3.connect(str(self._db_path)) as conn:
            rows = conn.execute(
                """SELECT frequency_hz, s11_real, s11_imag
                   FROM sweep_points WHERE sweep_id = ?
                   ORDER BY frequency_hz""",
                (sweep_id,),
            ).fetchall()

            return [
                SweepPoint(frequency_hz=r[0], s11_real=r[1], s11_imag=r[2])
                for r in rows
            ]

    def get_sweep_result(self, sweep_id: int) -> Optional[SweepResult]:
        """Get a full SweepResult (metadata + points) by ID."""
        sweep = self.get_sweep(sweep_id)
        if not sweep:
            return None

        points = self.get_sweep_points(sweep_id)
        return SweepResult(
            points=points,
            timestamp=sweep.timestamp,
            device_info=sweep.device_info,
        )

    def list_sweeps(self, limit: int = 50) -> List[StoredSweep]:
        """List stored sweeps, most recent first.

        Args:
            limit: Maximum number of results.

        Returns:
            List of StoredSweep metadata objects.
        """
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM sweeps ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()

            return [
                StoredSweep(
                    id=r["id"],
                    timestamp=r["timestamp"],
                    start_hz=r["start_hz"],
                    stop_hz=r["stop_hz"],
                    num_points=r["num_points"],
                    min_swr=r["min_swr"],
                    min_swr_freq_hz=r["min_swr_freq_hz"],
                    device_info=r["device_info"],
                    label=r["label"],
                )
                for r in rows
            ]

    def delete_sweep(self, sweep_id: int) -> bool:
        """Delete a sweep and its points.

        Returns:
            True if deleted, False if not found.
        """
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM sweep_points WHERE sweep_id = ?", (sweep_id,))
            cursor = conn.execute("DELETE FROM sweeps WHERE id = ?", (sweep_id,))
            conn.commit()
            return cursor.rowcount > 0

    def export_csv(self, sweep_id: int) -> str:
        """Export sweep data as CSV string.

        Returns:
            CSV-formatted string with header row.
        """
        points = self.get_sweep_points(sweep_id)
        if not points:
            return ""

        lines = ["frequency_hz,frequency_mhz,s11_real,s11_imag,swr,return_loss_db,resistance,reactance"]
        for p in points:
            lines.append(
                f"{p.frequency_hz},{p.frequency_mhz:.6f},"
                f"{p.s11_real:.8f},{p.s11_imag:.8f},"
                f"{p.swr:.4f},{p.return_loss_db:.2f},"
                f"{p.resistance:.2f},{p.reactance:.2f}"
            )
        return "\n".join(lines)

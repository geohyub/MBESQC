"""MBESQC DataService -- SQLite CRUD (thread-safe, static methods)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "mbesqc_desktop.db"
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection with WAL mode."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


class DataService:
    """Stateless adapter: all methods are @staticmethod."""

    @staticmethod
    def init_db():
        """Create tables if not exist."""
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                vessel      TEXT DEFAULT '',
                created_at  TEXT NOT NULL,
                pds_dir     TEXT DEFAULT '',
                gsf_dir     TEXT DEFAULT '',
                hvf_dir     TEXT DEFAULT '',
                s7k_dir     TEXT DEFAULT '',
                fau_dir     TEXT DEFAULT '',
                om_config_id TEXT DEFAULT '',
                max_pings   INTEGER DEFAULT 0,
                cell_size   REAL DEFAULT 5.0,
                max_gsf_files INTEGER DEFAULT 50,
                lat_min     REAL DEFAULT -90.0,
                lat_max     REAL DEFAULT 90.0,
                lon_min     REAL DEFAULT -180.0,
                lon_max     REAL DEFAULT 180.0
            );

            CREATE TABLE IF NOT EXISTS project_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename    TEXT NOT NULL,
                filepath    TEXT NOT NULL,
                size_mb     REAL DEFAULT 0.0,
                format      TEXT DEFAULT '',
                added_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS qc_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id     INTEGER REFERENCES project_files(id) ON DELETE CASCADE,
                project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                status      TEXT DEFAULT 'pending',
                score       REAL DEFAULT 0.0,
                grade       TEXT DEFAULT '',
                started_at  TEXT,
                finished_at TEXT,
                result_json TEXT DEFAULT '{}',
                output_dir  TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                action      TEXT NOT NULL,
                detail      TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            );
        """)
        conn.commit()

    # ── Projects ──

    @staticmethod
    def create_project(name: str, vessel: str = "", **kwargs) -> int:
        conn = _get_conn()
        cols = ["name", "vessel", "created_at"]
        vals = [name, vessel, datetime.now().isoformat()]

        for k in ("pds_dir", "gsf_dir", "hvf_dir", "s7k_dir", "fau_dir",
                   "om_config_id", "max_pings", "cell_size",
                   "lat_min", "lat_max", "lon_min", "lon_max"):
            if k in kwargs:
                cols.append(k)
                vals.append(kwargs[k])

        placeholders = ", ".join("?" * len(cols))
        col_str = ", ".join(cols)
        cur = conn.execute(
            f"INSERT INTO projects ({col_str}) VALUES ({placeholders})", vals)
        conn.commit()

        pid = cur.lastrowid
        DataService.log_activity(pid, "project_created", f"Project '{name}' created")
        return pid

    @staticmethod
    def get_project(project_id: int) -> Optional[dict]:
        row = _get_conn().execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_projects() -> list[dict]:
        rows = _get_conn().execute(
            "SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def update_project(project_id: int, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return
        vals.append(project_id)
        _get_conn().execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", vals)
        _get_conn().commit()

    @staticmethod
    def delete_project(project_id: int):
        conn = _get_conn()
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()

    # ── Project Files ──

    @staticmethod
    def add_file(project_id: int, filename: str, filepath: str,
                 size_mb: float = 0.0, fmt: str = "") -> int:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO project_files (project_id, filename, filepath, size_mb, format, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, filename, filepath, size_mb, fmt, datetime.now().isoformat()))
        conn.commit()
        return cur.lastrowid

    @staticmethod
    def get_project_files(project_id: int) -> list[dict]:
        rows = _get_conn().execute(
            "SELECT * FROM project_files WHERE project_id = ? ORDER BY filename",
            (project_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_file(file_id: int) -> Optional[dict]:
        row = _get_conn().execute(
            "SELECT * FROM project_files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def delete_file(file_id: int):
        conn = _get_conn()
        conn.execute("DELETE FROM project_files WHERE id = ?", (file_id,))
        conn.commit()

    # ── QC Results ──

    @staticmethod
    def create_qc_result(file_id: Optional[int], project_id: int) -> int:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO qc_results (file_id, project_id, status, started_at)
               VALUES (?, ?, 'running', ?)""",
            (file_id, project_id, datetime.now().isoformat()))
        conn.commit()
        return cur.lastrowid

    @staticmethod
    def update_qc_result(result_id: int, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return
        vals.append(result_id)
        _get_conn().execute(
            f"UPDATE qc_results SET {', '.join(sets)} WHERE id = ?", vals)
        _get_conn().commit()

    @staticmethod
    def get_qc_result(result_id: int) -> Optional[dict]:
        row = _get_conn().execute(
            "SELECT * FROM qc_results WHERE id = ?", (result_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_file_qc_results(file_id: int) -> list[dict]:
        rows = _get_conn().execute(
            "SELECT * FROM qc_results WHERE file_id = ? ORDER BY started_at DESC",
            (file_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_project_qc_results(project_id: int) -> list[dict]:
        rows = _get_conn().execute(
            "SELECT * FROM qc_results WHERE project_id = ? ORDER BY started_at DESC",
            (project_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_latest_file_result(file_id: int) -> Optional[dict]:
        row = _get_conn().execute(
            """SELECT * FROM qc_results WHERE file_id = ? AND status = 'done'
               ORDER BY finished_at DESC LIMIT 1""",
            (file_id,)).fetchone()
        return dict(row) if row else None

    # ── Activity Log ──

    @staticmethod
    def log_activity(project_id: int, action: str, detail: str = ""):
        _get_conn().execute(
            "INSERT INTO activity_log (project_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (project_id, action, detail, datetime.now().isoformat()))
        _get_conn().commit()

    @staticmethod
    def get_recent_activity(limit: int = 20) -> list[dict]:
        rows = _get_conn().execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── KPIs ──

    @staticmethod
    def get_kpis() -> dict:
        conn = _get_conn()
        total_projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        total_files = conn.execute("SELECT COUNT(*) FROM project_files").fetchone()[0]
        analyzed = conn.execute(
            "SELECT COUNT(*) FROM qc_results WHERE status = 'done'").fetchone()[0]

        avg_row = conn.execute(
            "SELECT AVG(score) FROM qc_results WHERE status = 'done'").fetchone()
        avg_score = avg_row[0] if avg_row[0] is not None else 0.0

        return {
            "total_projects": total_projects,
            "total_files": total_files,
            "analyzed": analyzed,
            "avg_score": round(avg_score, 1),
        }

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
_SYNCABLE_PROJECT_DIRS = ("pds_dir", "gsf_dir", "hvf_dir", "s7k_dir", "fau_dir")
_SYNCABLE_EXTS = {
    ".pds": "pds",
    ".gsf": "gsf",
    ".hvf": "hvf",
    ".s7k": "s7k",
    ".fau": "fau",
}


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
                file_id     INTEGER REFERENCES project_files(id) ON DELETE SET NULL,
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
        DataService._ensure_qc_results_file_fk(conn)
        conn.commit()

    @staticmethod
    def _ensure_qc_results_file_fk(conn: sqlite3.Connection):
        """Migrate qc_results.file_id FK to SET NULL to protect project snapshots."""
        fk_rows = conn.execute("PRAGMA foreign_key_list(qc_results)").fetchall()
        file_fk = next(
            (
                row for row in fk_rows
                if row["table"] == "project_files" and row["from"] == "file_id"
            ),
            None,
        )
        if not file_fk or str(file_fk["on_delete"]).upper() == "SET NULL":
            return

        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("DROP TABLE IF EXISTS qc_results__new")
            conn.execute("""
                CREATE TABLE qc_results__new (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id     INTEGER REFERENCES project_files(id) ON DELETE SET NULL,
                    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    status      TEXT DEFAULT 'pending',
                    score       REAL DEFAULT 0.0,
                    grade       TEXT DEFAULT '',
                    started_at  TEXT,
                    finished_at TEXT,
                    result_json TEXT DEFAULT '{}',
                    output_dir  TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                INSERT INTO qc_results__new (
                    id, file_id, project_id, status, score, grade,
                    started_at, finished_at, result_json, output_dir
                )
                SELECT
                    id, file_id, project_id, status, score, grade,
                    started_at, finished_at, result_json, output_dir
                FROM qc_results
            """)
            conn.execute("DROP TABLE qc_results")
            conn.execute("ALTER TABLE qc_results__new RENAME TO qc_results")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

    # ── Projects ──

    @staticmethod
    def create_project(name: str, vessel: str = "", **kwargs) -> int:
        conn = _get_conn()
        cols = ["name", "vessel", "created_at"]
        vals = [name, vessel, datetime.now().isoformat()]

        for k in ("pds_dir", "gsf_dir", "hvf_dir", "s7k_dir", "fau_dir",
                   "om_config_id", "max_pings", "max_gsf_files", "cell_size",
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
        conn.execute("UPDATE qc_results SET file_id = NULL WHERE file_id = ?", (file_id,))
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

    @staticmethod
    def get_latest_project_result(project_id: int) -> Optional[dict]:
        row = _get_conn().execute(
            """SELECT * FROM qc_results WHERE project_id = ? AND status = 'done'
               ORDER BY finished_at DESC LIMIT 1""",
            (project_id,)).fetchone()
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

    @staticmethod
    def sync_project_files(project_id: int) -> dict:
        """Index top-level files from the project's configured directories."""
        project = DataService.get_project(project_id)
        if not project:
            return {"added": 0, "updated": 0, "skipped": 0, "indexed": 0}

        conn = _get_conn()
        existing_rows = conn.execute(
            "SELECT id, filename, filepath, size_mb, format FROM project_files WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        existing_by_path = {
            str(Path(r["filepath"])).lower(): dict(r)
            for r in existing_rows
        }

        added = 0
        updated = 0
        skipped = 0

        for field in _SYNCABLE_PROJECT_DIRS:
            raw_path = (project.get(field) or "").strip()
            if not raw_path:
                continue

            path = Path(raw_path)
            candidates: list[Path] = []
            if path.is_file():
                if path.suffix.lower() in _SYNCABLE_EXTS:
                    candidates = [path]
            elif path.is_dir():
                candidates = [
                    child for child in sorted(path.iterdir())
                    if child.is_file() and child.suffix.lower() in _SYNCABLE_EXTS
                ]
            else:
                continue

            for file_path in candidates:
                normalized = str(file_path).lower()
                filename = file_path.name
                fmt = _SYNCABLE_EXTS[file_path.suffix.lower()]
                size_mb = round(file_path.stat().st_size / (1024 * 1024), 3)
                existing = existing_by_path.get(normalized)

                if existing:
                    if (
                        existing.get("filename") != filename
                        or round(float(existing.get("size_mb") or 0.0), 3) != size_mb
                        or (existing.get("format") or "").lower() != fmt
                    ):
                        conn.execute(
                            """UPDATE project_files
                               SET filename = ?, size_mb = ?, format = ?
                               WHERE id = ?""",
                            (filename, size_mb, fmt, existing["id"]),
                        )
                        updated += 1
                    else:
                        skipped += 1
                    continue

                cur = conn.execute(
                    """INSERT INTO project_files (project_id, filename, filepath, size_mb, format, added_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        filename,
                        str(file_path),
                        size_mb,
                        fmt,
                        datetime.now().isoformat(),
                    ),
                )
                existing_by_path[normalized] = {
                    "id": cur.lastrowid,
                    "filename": filename,
                    "filepath": str(file_path),
                    "size_mb": size_mb,
                    "format": fmt,
                }
                added += 1

        conn.commit()

        indexed = added + updated + skipped
        if added or updated:
            DataService.log_activity(
                project_id,
                "project_files_synced",
                f"Indexed {indexed} files from configured paths (added {added}, updated {updated})",
            )

        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "indexed": indexed,
        }

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

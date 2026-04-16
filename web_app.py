"""MBES QC — Flask Web Application

Multibeam Echosounder data quality control web interface.
PDS 파일 하나 넣으면 전체 QC 자동 실행.

Port: 5103
Copyright (c) 2025-2026 Geoview Co., Ltd.
"""

from __future__ import annotations

import os
import sys
import json
import logging
import time
import threading
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, send_file

sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get("MBESQC_SECRET", os.urandom(24).hex())
logger = logging.getLogger(__name__)


@app.template_filter('basename')
def basename_filter(path):
    return Path(path).name if path else ''

# ── In-memory storage ─────────────────────────────────────

_jobs: dict[int, dict] = {}
_projects: dict[int, dict] = {}
_job_counter = 0
_project_counter = 0
_lock = threading.Lock()


class OMResolution(TypedDict, total=False):
    source: Literal["api", "request", "project", "unresolved"]
    source_label: str
    path: str
    exists: bool
    mode: Literal["api-first", "explicit-sqlite-fallback"]
    fallback_scope: Literal["request", "project", "none"]
    request_path_supplied: bool
    request_fallback_enabled: bool
    project_fallback_configured: bool
    project_fallback_enabled: bool
    hint: str
    semantic_checks: list[dict]


class OMProvenance(TypedDict, total=False):
    decision: dict
    fallback_candidate: dict
    semantic: dict
    resolution_chain: list[dict]


def _coerce_optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _flag_is_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _project_for_om_lookup(project_id: int | str | None = None) -> dict | None:
    if project_id in (None, ""):
        return None
    try:
        return _projects.get(int(project_id))
    except (TypeError, ValueError):
        return None


def _normalize_om_text(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _project_sqlite_fallback_status(project: dict | None) -> dict:
    """Inspect a project-stored OffsetManager DB path for semantic trust.

    The legacy web path only trusts confirmed project DBs when the stored path
    exists and the content still looks like the same project, not just any
    SQLite file with a matching filename.
    """
    status = {
        "configured": False,
        "confirmed": False,
        "enabled": False,
        "path": "",
        "path_exists": False,
        "schema_ok": False,
        "schema_version": None,
        "record_count": 0,
        "semantic_state": "unconfigured",
        "semantic_hint": "OffsetManager API를 우선 사용합니다. 프로젝트에 저장된 DB 경로나 요청 경로가 없으면 명시적 SQLite fallback은 사용하지 않습니다.",
        "stale_risk": False,
        "latest_vessel_name": "",
        "latest_project_name": "",
        "latest_config_date": "",
        "latest_updated_at": "",
        "latest_review_state": "",
        "project_vessel_match": None,
        "project_name_match": None,
        "semantic_checks": [],
    }
    if not project:
        return status

    project_path = project.get("offset_db_path") or project.get("offset_db")
    if not project_path:
        return status

    status["configured"] = True
    status["confirmed"] = _flag_is_enabled(project.get("offset_db_path_confirmed"))
    path = Path(str(project_path)).expanduser()
    status["path"] = str(path)
    checks: list[dict] = []

    def _append_check(name: str, passed: bool, detail: str, *, severity: str = "info", blocking: bool = False) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "blocking": bool(blocking),
                "severity": severity,
                "detail": detail,
            }
        )

    def _finalize(state: str, hint: str, *, enabled: bool = False) -> dict:
        status["semantic_state"] = state
        status["semantic_hint"] = hint
        status["enabled"] = enabled
        status["semantic_checks"] = checks
        return status

    if not path.exists():
        _append_check(
            "project_db_path_exists",
            False,
            "프로젝트에 저장된 OffsetManager DB 경로가 존재하지 않습니다.",
            severity="error",
            blocking=True,
        )
        return _finalize("missing", "프로젝트에 저장된 OffsetManager DB 경로가 존재하지 않습니다.")

    status["path_exists"] = True
    _append_check("project_db_path_exists", True, "프로젝트 저장 경로가 존재합니다.")
    try:
        import sqlite3

        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                user_version_row = conn.execute("PRAGMA user_version").fetchone()
                status["schema_version"] = int(user_version_row[0] or 0) if user_version_row else 0
            except Exception:
                status["schema_version"] = None

            tables = {
                row["name"]
                for row in (
                    conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                )
            }
            required_tables = {"vessel_configs", "sensor_offsets"}
            missing_tables = sorted(required_tables - tables)
            if missing_tables:
                _append_check(
                    "required_tables_present",
                    False,
                    "프로젝트 저장 DB가 OffsetManager의 필수 테이블을 모두 포함하지 않습니다: "
                    + ", ".join(missing_tables),
                    severity="error",
                    blocking=True,
                )
                return _finalize(
                    "schema-risk",
                    "프로젝트 저장 DB가 OffsetManager의 필수 테이블을 모두 포함하지 않습니다: "
                    + ", ".join(missing_tables),
                )
            _append_check("required_tables_present", True, "필수 테이블(vessel_configs, sensor_offsets)이 존재합니다.")

            vessel_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(vessel_configs)").fetchall()
            }
            required_vessel_columns = {"id", "vessel_name", "project_name", "config_date", "review_state", "updated_at"}
            missing_vessel_columns = sorted(required_vessel_columns - vessel_columns)
            if missing_vessel_columns:
                _append_check(
                    "vessel_configs_schema",
                    False,
                    "프로젝트 저장 DB의 vessel_configs 스키마가 기대값과 다릅니다: "
                    + ", ".join(missing_vessel_columns),
                    severity="error",
                    blocking=True,
                )
                return _finalize(
                    "schema-risk",
                    "프로젝트 저장 DB의 vessel_configs 스키마가 기대값과 다릅니다: "
                    + ", ".join(missing_vessel_columns),
                )
            _append_check("vessel_configs_schema", True, "vessel_configs 스키마가 기대값과 일치합니다.")

            sensor_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sensor_offsets)").fetchall()
            }
            required_sensor_columns = {"config_id", "sensor_name", "sensor_type"}
            missing_sensor_columns = sorted(required_sensor_columns - sensor_columns)
            if missing_sensor_columns:
                _append_check(
                    "sensor_offsets_schema",
                    False,
                    "프로젝트 저장 DB의 sensor_offsets 스키마가 기대값과 다릅니다: "
                    + ", ".join(missing_sensor_columns),
                    severity="error",
                    blocking=True,
                )
                return _finalize(
                    "schema-risk",
                    "프로젝트 저장 DB의 sensor_offsets 스키마가 기대값과 다릅니다: "
                    + ", ".join(missing_sensor_columns),
                )
            _append_check("sensor_offsets_schema", True, "sensor_offsets 스키마가 기대값과 일치합니다.")

            record_count = conn.execute("SELECT COUNT(*) FROM vessel_configs").fetchone()
            status["record_count"] = int(record_count[0] or 0) if record_count else 0
            if status["record_count"] == 0:
                _append_check(
                    "vessel_configs_has_records",
                    False,
                    "vessel_configs에 레코드가 없어 프로젝트 저장 DB가 오래되었거나 비어 있을 수 있습니다.",
                    severity="error",
                    blocking=True,
                )
                return _finalize(
                    "empty",
                    "vessel_configs에 레코드가 없어 프로젝트 저장 DB가 오래되었거나 비어 있을 수 있습니다.",
                )
            _append_check("vessel_configs_has_records", True, "vessel_configs에 레코드가 있습니다.")

            status["schema_ok"] = True

            latest = conn.execute(
                """
                SELECT id, vessel_name, project_name, config_date, updated_at, review_state
                FROM vessel_configs
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if not latest:
                _append_check(
                    "latest_vessel_config_readable",
                    False,
                    "vessel_configs의 최신 레코드를 읽지 못했습니다. 오래된 DB일 수 있습니다.",
                    severity="error",
                    blocking=True,
                )
                return _finalize(
                    "empty",
                    "vessel_configs의 최신 레코드를 읽지 못했습니다. 오래된 DB일 수 있습니다.",
                )
            _append_check("latest_vessel_config_readable", True, "vessel_configs의 최신 레코드를 읽었습니다.")

            status["latest_vessel_name"] = str(latest["vessel_name"] or "")
            status["latest_project_name"] = str(latest["project_name"] or "")
            status["latest_config_date"] = str(latest["config_date"] or "")
            status["latest_updated_at"] = str(latest["updated_at"] or "")
            status["latest_review_state"] = str(latest["review_state"] or "")

            project_vessel = _normalize_om_text(project.get("vessel"))
            project_name = _normalize_om_text(project.get("name"))
            latest_vessel = _normalize_om_text(status["latest_vessel_name"])
            latest_project = _normalize_om_text(status["latest_project_name"])

            vessel_match = bool(project_vessel and latest_vessel and project_vessel == latest_vessel)
            project_match = bool(project_name and latest_project and project_name == latest_project)
            status["project_vessel_match"] = vessel_match if project_vessel else None
            status["project_name_match"] = project_match if project_name else None
            if project_vessel or project_name:
                identity_match = vessel_match or project_match
                _append_check(
                    "project_identity_match",
                    identity_match,
                    (
                        "프로젝트 메타데이터와 DB 최신 config가 일치합니다."
                        if identity_match
                        else (
                            "프로젝트 메타데이터("
                            + " / ".join([bit for bit in [project.get("name"), project.get("vessel")] if bit] or ["project metadata unavailable"])
                            + ")와 DB 최신 config("
                            + " / ".join([bit for bit in [status["latest_project_name"], status["latest_vessel_name"]] if bit] or ["latest config unavailable"])
                            + ")가 일치하지 않습니다."
                        )
                    ),
                    severity="error" if not identity_match else "info",
                    blocking=not identity_match,
                )

            if (project_vessel or project_name) and not (vessel_match or project_match):
                status["stale_risk"] = True
                expected_bits = [bit for bit in [project.get("name"), project.get("vessel")] if bit]
                latest_bits = [bit for bit in [status["latest_project_name"], status["latest_vessel_name"]] if bit]
                return _finalize(
                    "stale-risk",
                    "프로젝트 메타데이터("
                    + " / ".join(expected_bits or ["project metadata unavailable"])
                    + ")와 DB 최신 config("
                    + " / ".join(latest_bits or ["latest config unavailable"])
                    + ")가 일치하지 않아 다른 프로젝트나 오래된 DB로 판단했습니다.",
                )

            review_state = _normalize_om_text(status["latest_review_state"])
            review_state_ok = review_state in {"approved", "reviewed"}
            if status["latest_review_state"]:
                _append_check(
                    "review_state_approved",
                    review_state_ok,
                    (
                        "최신 config review_state가 승인/검토 상태입니다."
                        if review_state_ok
                        else f"최신 config review_state가 '{status['latest_review_state']}'라 fallback을 허용하지 않습니다."
                    ),
                    severity="error" if not review_state_ok else "info",
                    blocking=not review_state_ok,
                )
            else:
                _append_check(
                    "review_state_approved",
                    False,
                    "최신 config review_state가 비어 있어 confirmed fallback을 허용하지 않습니다.",
                    severity="error",
                    blocking=True,
                )
                return _finalize(
                    "review-risk",
                    "최신 config review_state가 비어 있어 confirmed fallback을 허용하지 않습니다.",
                )

            if not review_state_ok:
                return _finalize(
                    "review-risk",
                    f"최신 config review_state가 '{status['latest_review_state']}'라 confirmed fallback을 허용하지 않습니다.",
                )

            _append_check(
                "fallback_confirmation",
                status["confirmed"],
                "프로젝트 저장 DB fallback 확인이 켜져 있습니다." if status["confirmed"] else "프로젝트 저장 DB fallback 확인이 꺼져 있습니다.",
                severity="info" if status["confirmed"] else "error",
                blocking=not status["confirmed"],
            )
            status["semantic_state"] = "verified"
            status["semantic_hint"] = (
                "프로젝트 메타데이터와 OffsetManager 최신 config가 일치합니다."
                if (project_vessel or project_name)
                else "OffsetManager DB 구조와 최신 config를 읽었습니다. 프로젝트 메타데이터가 비어 있어 identity 비교는 생략했습니다."
            )
            status["enabled"] = status["confirmed"]
            status["semantic_checks"] = checks
            return status
    except Exception as exc:
        status["semantic_state"] = "schema-risk"
        status["semantic_hint"] = f"프로젝트 저장 DB를 검사하지 못했습니다: {exc}"
        status["semantic_checks"] = checks + [
            {
                "name": "semantic_probe",
                "passed": False,
                "blocking": True,
                "severity": "error",
                "detail": f"프로젝트 저장 DB 검사 예외: {exc}",
            }
        ]
        return status

    return status


def _request_sqlite_fallback_status(request_path: str | Path, allow_request_path: bool) -> dict:
    """Inspect a request-supplied OffsetManager DB without project metadata reuse."""
    fake_project = {
        "offset_db_path": str(request_path),
        "offset_db_path_confirmed": allow_request_path,
    }
    status = _project_sqlite_fallback_status(fake_project)

    normalized_checks: list[dict] = []
    for check in status.get("semantic_checks", []):
        normalized = dict(check)
        if normalized.get("name") == "project_db_path_exists":
            normalized["name"] = "request_db_path_exists"
            normalized["detail"] = (
                "요청한 OffsetManager DB 경로가 존재합니다."
                if normalized.get("passed")
                else "요청한 OffsetManager DB 경로가 존재하지 않습니다."
            )
        elif normalized.get("name") == "fallback_confirmation":
            normalized["name"] = "request_path_allowed"
            normalized["detail"] = (
                "명시적 SQLite fallback 요청이 허용되었습니다."
                if normalized.get("passed")
                else "명시적 SQLite fallback 요청이 허용되지 않았습니다."
            )
        normalized_checks.append(normalized)

    status["semantic_checks"] = normalized_checks
    if status.get("semantic_state") == "verified":
        status["semantic_hint"] = "요청 SQLite DB의 구조와 최신 config를 읽었습니다."
    elif status.get("semantic_state") == "review-risk":
        latest_review_state = status.get("latest_review_state", "")
        status["semantic_hint"] = (
            f"요청 SQLite DB의 최신 config review_state가 '{latest_review_state}'라 사용을 보류합니다."
            if latest_review_state
            else "요청 SQLite DB의 최신 config review_state가 비어 있어 사용을 보류합니다."
        )
    elif status.get("semantic_state") == "missing":
        status["semantic_hint"] = "요청 SQLite DB 경로가 존재하지 않습니다."
    elif status.get("semantic_state") == "schema-risk":
        status["semantic_hint"] = "요청 SQLite DB의 스키마를 OffsetManager 기준으로 확인하지 못했습니다."
    elif status.get("semantic_state") == "empty":
        status["semantic_hint"] = "요청 SQLite DB의 vessel_configs에 레코드가 없습니다."

    status["configured"] = True
    status["confirmed"] = bool(allow_request_path)
    return status


def _project_sqlite_fallback_state(project: dict | None) -> tuple[bool, bool]:
    """Return whether a project has a stored path and whether it is confirmed."""
    status = _project_sqlite_fallback_status(project)
    configured = bool(status["configured"])
    enabled = configured and bool(status["enabled"])
    return configured, enabled


def _resolve_om_db_path(
    db_path: str | None = None,
    project_id: int | str | None = None,
    project: dict | None = None,
    allow_request_path: bool = False,
) -> Path | None:
    """Resolve an explicit OffsetManager DB path, if one is configured.

    Only request-local paths or explicitly confirmed project-saved paths are
    considered here. Hidden environment fallback would make the legacy web
    boundary harder to reason about, so the caller must opt in through a
    request value or an explicit project confirmation flag.
    """
    if db_path and allow_request_path:
        explicit = Path(db_path).expanduser()
        return explicit if explicit.exists() else None

    if project is None:
        project = _project_for_om_lookup(project_id)
    project_status = _project_sqlite_fallback_status(project)
    if project_status["configured"] and project_status["enabled"] and project:
        project_path = project.get("offset_db_path") or project.get("offset_db")
        if project_path:
            resolved = Path(str(project_path)).expanduser()
            return resolved if resolved.exists() else None

    return None


def _describe_om_resolution(
    db_path: str | None = None,
    project_id: int | str | None = None,
    project: dict | None = None,
    allow_request_path: bool = False,
) -> OMResolution:
    """Describe which OffsetManager source is currently usable."""
    request_supplied = bool(db_path)
    explicit = Path(db_path).expanduser() if request_supplied else None
    request_exists = bool(explicit and explicit.exists())
    if project is None:
        project = _project_for_om_lookup(project_id)
    project_status = _project_sqlite_fallback_status(project)
    project_configured = bool(project_status["configured"])
    project_enabled = bool(project_status["enabled"])

    if request_supplied and allow_request_path and request_exists:
        request_status = _request_sqlite_fallback_status(explicit, allow_request_path)
        return {
            "source": "request",
            "source_label": "요청 DB 경로",
            "path": str(explicit),
            "exists": explicit.exists(),
            "mode": "explicit-sqlite-fallback",
            "fallback_scope": "request",
            "request_path_supplied": True,
            "request_fallback_enabled": True,
            "project_fallback_configured": project_configured,
            "project_fallback_enabled": project_enabled,
            "semantic_state": request_status["semantic_state"],
            "semantic_hint": request_status["semantic_hint"],
            "semantic_checks": request_status["semantic_checks"],
            "schema_version": request_status["schema_version"],
            "latest_vessel_name": request_status["latest_vessel_name"],
            "latest_project_name": request_status["latest_project_name"],
            "latest_config_date": request_status["latest_config_date"],
            "latest_updated_at": request_status["latest_updated_at"],
            "latest_review_state": request_status["latest_review_state"],
            "stale_risk": request_status["stale_risk"],
            "schema_ok": request_status["schema_ok"],
            "hint": request_status["semantic_hint"] or "요청에서 전달된 SQLite 경로를 사용할 수 있습니다.",
        }

    if project_configured and project and project_enabled:
        project_path = project.get("offset_db_path") or project.get("offset_db")
        if project_path:
            resolved = Path(str(project_path)).expanduser()
            request_note = ""
            if request_supplied:
                if allow_request_path and not request_exists:
                    request_note = " 요청 DB 경로는 존재하지 않아 프로젝트 저장 DB 경로를 사용했습니다."
                elif not allow_request_path:
                    request_note = " 요청 DB 경로는 '명시적 SQLite fallback 사용'이 꺼져 있어 무시되었습니다."
            return {
                "source": "project",
                "source_label": "프로젝트 저장 DB 경로",
                "path": str(resolved),
                "exists": resolved.exists(),
                "mode": "explicit-sqlite-fallback",
                "fallback_scope": "project",
                "request_path_supplied": request_supplied,
                "request_fallback_enabled": allow_request_path,
                "project_fallback_configured": project_configured,
                "project_fallback_enabled": project_enabled,
                "semantic_state": project_status["semantic_state"],
                "semantic_hint": project_status["semantic_hint"],
                "semantic_checks": project_status["semantic_checks"],
                "schema_version": project_status["schema_version"],
                "latest_vessel_name": project_status["latest_vessel_name"],
                "latest_project_name": project_status["latest_project_name"],
                "latest_config_date": project_status["latest_config_date"],
                "latest_updated_at": project_status["latest_updated_at"],
                "latest_review_state": project_status["latest_review_state"],
                "stale_risk": project_status["stale_risk"],
                "schema_ok": project_status["schema_ok"],
                "hint": ("프로젝트에 저장된 SQLite 경로를 사용할 수 있습니다." if resolved.exists() else "프로젝트의 OffsetManager 경로가 존재하지 않습니다.") + request_note,
            }

    if request_supplied:
        assert explicit is not None
        request_note = "요청 DB 경로가 존재하지 않습니다."
        if not allow_request_path:
            request_note = "요청 DB 경로가 전달됐지만 '명시적 SQLite fallback 사용'이 꺼져 있어 무시되었습니다."
        if project_configured and not project_enabled:
            request_note += " 프로젝트에 저장된 DB 경로가 있어도 명시적 프로젝트 fallback 확인이 꺼져 있어 사용하지 않았습니다."
        return {
            "source": "unresolved",
            "source_label": "요청 DB 경로(미사용)",
            "path": str(explicit),
            "exists": request_exists,
            "mode": "api-first",
            "fallback_scope": "none",
            "request_path_supplied": True,
            "request_fallback_enabled": allow_request_path,
            "project_fallback_configured": project_configured,
            "project_fallback_enabled": project_enabled,
            "semantic_state": project_status["semantic_state"],
            "semantic_hint": project_status["semantic_hint"],
            "semantic_checks": project_status["semantic_checks"],
            "schema_version": project_status["schema_version"],
            "latest_vessel_name": project_status["latest_vessel_name"],
            "latest_project_name": project_status["latest_project_name"],
            "latest_config_date": project_status["latest_config_date"],
            "latest_updated_at": project_status["latest_updated_at"],
            "latest_review_state": project_status["latest_review_state"],
            "stale_risk": project_status["stale_risk"],
            "schema_ok": project_status["schema_ok"],
            "hint": request_note,
        }

    if project_configured and not project_enabled and project:
        stored_path = project.get("offset_db_path") or project.get("offset_db") or ""
        resolved = Path(str(stored_path)).expanduser() if stored_path else None
        return {
            "source": "unresolved",
            "source_label": "프로젝트 저장 DB 경로(미사용)",
            "path": str(resolved) if resolved else "",
            "exists": bool(resolved and resolved.exists()),
            "mode": "api-first",
            "fallback_scope": "none",
            "request_path_supplied": False,
            "request_fallback_enabled": False,
            "project_fallback_configured": True,
            "project_fallback_enabled": False,
            "semantic_state": project_status["semantic_state"],
            "semantic_hint": project_status["semantic_hint"],
            "semantic_checks": project_status["semantic_checks"],
            "schema_version": project_status["schema_version"],
            "latest_vessel_name": project_status["latest_vessel_name"],
            "latest_project_name": project_status["latest_project_name"],
            "latest_config_date": project_status["latest_config_date"],
            "latest_updated_at": project_status["latest_updated_at"],
            "latest_review_state": project_status["latest_review_state"],
            "stale_risk": project_status["stale_risk"],
            "schema_ok": project_status["schema_ok"],
            "hint": (
                (project_status["semantic_hint"] or "프로젝트에 저장된 SQLite 경로를 확인했습니다.")
                + (
                    " 프로젝트 저장 DB fallback 확인이 꺼져 있어 사용하지 않았습니다."
                    if project_status["semantic_state"] == "verified"
                    else ""
                )
            ),
        }

    return {
        "source": "unresolved",
        "source_label": "미해결",
        "path": "",
        "exists": False,
        "mode": "api-first",
        "fallback_scope": "none",
        "request_path_supplied": False,
        "request_fallback_enabled": False,
        "project_fallback_configured": project_configured,
        "project_fallback_enabled": project_enabled,
        "semantic_state": project_status["semantic_state"],
        "semantic_hint": project_status["semantic_hint"],
        "semantic_checks": project_status["semantic_checks"],
        "schema_version": project_status["schema_version"],
        "latest_vessel_name": project_status["latest_vessel_name"],
        "latest_project_name": project_status["latest_project_name"],
        "latest_config_date": project_status["latest_config_date"],
        "latest_updated_at": project_status["latest_updated_at"],
        "latest_review_state": project_status["latest_review_state"],
        "stale_risk": project_status["stale_risk"],
        "schema_ok": project_status["schema_ok"],
        "hint": project_status["semantic_hint"] or "OffsetManager API를 우선 사용합니다. 프로젝트에 저장된 DB 경로나 요청 경로가 없으면 명시적 SQLite fallback은 사용하지 않습니다.",
    }


def _describe_om_api_source() -> OMResolution:
    """Describe a successful API read from OffsetManager."""
    return {
        "source": "api",
        "source_label": "OffsetManager API",
        "path": "",
        "exists": False,
        "mode": "api-first",
        "fallback_scope": "none",
        "request_path_supplied": False,
        "request_fallback_enabled": False,
        "semantic_state": "verified",
        "semantic_hint": "OffsetManager API 응답을 사용했습니다.",
        "hint": "OffsetManager API 응답을 사용했습니다. 명시적 SQLite fallback은 사용하지 않았습니다.",
        "semantic_checks": [
            {
                "name": "api_available",
                "passed": True,
                "blocking": False,
                "severity": "info",
                "detail": "OffsetManager API 응답을 사용했습니다.",
            }
        ],
    }


def _snapshot_om_resolution(resolution: dict | None) -> dict:
    """Return a stable machine-readable subset of an OM resolution payload."""
    if not resolution:
        return {}

    fields = (
        "source",
        "source_label",
        "path",
        "exists",
        "mode",
        "fallback_scope",
        "request_path_supplied",
        "request_fallback_enabled",
        "project_fallback_configured",
        "project_fallback_enabled",
        "semantic_state",
        "semantic_hint",
        "semantic_checks",
        "schema_version",
        "schema_ok",
        "latest_vessel_name",
        "latest_project_name",
        "latest_config_date",
        "latest_updated_at",
        "latest_review_state",
        "project_vessel_match",
        "project_name_match",
        "stale_risk",
        "hint",
    )
    snapshot = {}
    for field in fields:
        if field in resolution:
            snapshot[field] = resolution.get(field)
    return snapshot


def _build_om_provenance(
    fallback_candidate: dict | None,
    decision: dict | None = None,
) -> OMProvenance:
    """Compose a typed provenance envelope for machine-readable MBESQC responses."""
    selected = decision or fallback_candidate or {}
    fallback = fallback_candidate or {}
    semantic_source = selected
    return {
        "decision": _snapshot_om_resolution(selected),
        "fallback_candidate": _snapshot_om_resolution(fallback),
        "semantic": {
            "state": semantic_source.get("semantic_state", ""),
            "hint": semantic_source.get("semantic_hint", semantic_source.get("hint", "")),
            "checks": semantic_source.get("semantic_checks", []),
            "schema_version": semantic_source.get("schema_version"),
            "schema_ok": bool(semantic_source.get("schema_ok", False)),
            "stale_risk": bool(semantic_source.get("stale_risk", False)),
            "latest_vessel_name": semantic_source.get("latest_vessel_name", ""),
            "latest_project_name": semantic_source.get("latest_project_name", ""),
            "latest_config_date": semantic_source.get("latest_config_date", ""),
            "latest_updated_at": semantic_source.get("latest_updated_at", ""),
            "latest_review_state": semantic_source.get("latest_review_state", ""),
            "project_vessel_match": semantic_source.get("project_vessel_match"),
            "project_name_match": semantic_source.get("project_name_match"),
        },
        "resolution_chain": [
            _snapshot_om_resolution(fallback),
            _snapshot_om_resolution(selected),
        ],
    }


def _normalize_om_detail(detail: dict) -> tuple[dict, list[dict]]:
    config = detail.get("config", detail)
    offsets = detail.get("offsets", detail.get("sensors", []))
    return config, offsets


def _normalize_om_project_fields(project: dict, data: dict) -> None:
    """Keep OM-related project fields consistent across create/edit flows."""
    if "om_config_id" in data or "offset_config_id" in data:
        om_config_value = data.get("om_config_id", data.get("offset_config_id", ""))
        project["om_config_id"] = _coerce_optional_int(om_config_value)
        project["offset_config_id"] = project["om_config_id"]

    if "offset_db_path" in data:
        raw_path = data.get("offset_db_path", "")
        previous_path = project.get("offset_db_path", "") or project.get("offset_db", "")
        project["offset_db_path"] = str(Path(raw_path).expanduser()) if raw_path else ""
        if "offset_db_path_confirmed" in data:
            project["offset_db_path_confirmed"] = _flag_is_enabled(data.get("offset_db_path_confirmed"))
        elif project["offset_db_path"] != previous_path:
            project["offset_db_path_confirmed"] = False

    if "offset_db_path_confirmed" in data and "offset_db_path" not in data:
        project["offset_db_path_confirmed"] = _flag_is_enabled(data.get("offset_db_path_confirmed"))


def _enforce_project_db_confirmation(project: dict) -> dict:
    """Clear confirmed fallback when the stored project DB fails semantic checks."""
    status = _project_sqlite_fallback_status(project)
    requested_confirmed = _flag_is_enabled(project.get("offset_db_path_confirmed"))
    if requested_confirmed and (not status["configured"] or not status["enabled"]):
        project["offset_db_path_confirmed"] = False
    elif not status["configured"]:
        project["offset_db_path_confirmed"] = False
    return status


def _next_id(counter_name: str) -> int:
    global _job_counter, _project_counter
    with _lock:
        if counter_name == 'job':
            _job_counter += 1
            return _job_counter
        else:
            _project_counter += 1
            return _project_counter


# ── Routes ─────────────────────────────────────────────────

@app.route("/")
def dashboard():
    completed = [j for j in _jobs.values() if j.get("status") == "done"]
    completed.sort(key=lambda j: j.get("finished_at", ""), reverse=True)
    return render_template("dashboard.html", jobs=completed, total_jobs=len(_jobs),
                           projects=list(_projects.values()))


@app.route("/new-project", methods=["GET", "POST"])
def new_project():
    """Create a new project and register data files."""
    if request.method == "POST":
        data = request.form
        proj_id = _next_id('project')
        project = {
            "id": proj_id,
            "name": data.get("project_name", f"Project {proj_id}"),
            "vessel": data.get("vessel_name", ""),
            "created_at": datetime.now().isoformat(),
            "pds_files": [],
            "gsf_dir": data.get("gsf_dir", ""),
            "hvf_dir": data.get("hvf_dir", ""),
            "s7k_dir": data.get("s7k_dir", ""),
            "fau_dir": data.get("fau_dir", ""),
            "om_config_id": data.get("om_config_id", ""),
            "offset_config_id": None,
            "offset_db_path": "",
            "jobs": [],
        }
        _normalize_om_project_fields(project, data)

        # Auto-scan PDS directory
        pds_dir = data.get("pds_dir", "")
        if pds_dir and Path(pds_dir).is_dir():
            for f in sorted(Path(pds_dir).glob("*.pds")):
                project["pds_files"].append({
                    "path": str(f),
                    "name": f.name,
                    "size_mb": f.stat().st_size / 1024 / 1024,
                })
        # Or single PDS file
        pds_file = data.get("pds_file", "")
        if pds_file and Path(pds_file).exists():
            f = Path(pds_file)
            project["pds_files"].append({
                "path": str(f),
                "name": f.name,
                "size_mb": f.stat().st_size / 1024 / 1024,
            })

        project_status = _enforce_project_db_confirmation(project)
        _projects[proj_id] = project
        if _flag_is_enabled(data.get("offset_db_path_confirmed")) and not project_status["enabled"]:
            flash(project_status["semantic_hint"] or "프로젝트 저장 DB fallback 확인이 저장되지 않았습니다.", "warning")
        flash(f"Project '{project['name']}' created with {len(project['pds_files'])} PDS files.", "success")
        return redirect(url_for("view_project", project_id=proj_id))

    om_configs = _load_om_configs()
    return render_template("new_project.html", om_configs=om_configs)


@app.route("/project/<int:project_id>")
def view_project(project_id):
    """View project with registered files and QC results."""
    project = _projects.get(project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("dashboard"))
    project_jobs = [_jobs[jid] for jid in project.get("jobs", []) if jid in _jobs]
    return render_template("project.html", project=project, jobs=project_jobs,
                           om_configs=_load_om_configs())


@app.route("/project/<int:project_id>/edit", methods=["POST"])
def edit_project(project_id):
    """Update project settings."""
    project = _projects.get(project_id)
    if not project:
        return jsonify({"error": "프로젝트를 찾을 수 없습니다"}), 404

    data = request.form or request.json or {}
    if data.get("project_name"):
        project["name"] = data["project_name"]
    if data.get("vessel_name"):
        project["vessel"] = data["vessel_name"]
    if "gsf_dir" in data:
        project["gsf_dir"] = data["gsf_dir"]
    if "hvf_dir" in data:
        project["hvf_dir"] = data["hvf_dir"]
    if "s7k_dir" in data:
        project["s7k_dir"] = data["s7k_dir"]
    if "fau_dir" in data:
        project["fau_dir"] = data["fau_dir"]
    _normalize_om_project_fields(project, data)
    project_status = _enforce_project_db_confirmation(project)

    # Re-scan PDS files if dir changed
    if data.get("pds_dir") and Path(data["pds_dir"]).is_dir():
        pds_files = sorted(Path(data["pds_dir"]).glob("*.pds"))
        project["pds_files"] = [
            {"path": str(p), "name": p.name, "size_mb": p.stat().st_size / 1024 / 1024}
            for p in pds_files
        ]

    if _flag_is_enabled(data.get("offset_db_path_confirmed")) and not project_status["enabled"]:
        flash(project_status["semantic_hint"] or "프로젝트 저장 DB fallback 확인이 저장되지 않았습니다.", "warning")
    flash("프로젝트가 수정되었습니다.", "success")
    return redirect(url_for("view_project", project_id=project_id))


@app.route("/project/<int:project_id>/delete", methods=["POST"])
def delete_project(project_id):
    """Delete a project."""
    project = _projects.pop(project_id, None)
    if not project:
        flash("프로젝트를 찾을 수 없습니다.", "danger")
    else:
        # Also remove associated jobs
        for jid in project.get("jobs", []):
            _jobs.pop(jid, None)
        flash(f"프로젝트 '{project['name']}'이(가) 삭제되었습니다.", "success")
    return redirect(url_for("dashboard"))


@app.route("/new-qc")
def new_qc():
    om_configs = _load_om_configs()
    return render_template("new_qc.html", om_configs=om_configs)


@app.route("/qc/<int:job_id>")
def qc_result(job_id):
    job = _jobs.get(job_id)
    if not job:
        flash("QC job not found.", "danger")
        return redirect(url_for("dashboard"))
    return render_template("qc_result.html", job=job)


# ── API Endpoints ──────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "module": "MBES QC", "port": 5103, "jobs": len(_jobs)})


def _validate_pds_path(pds_path: str) -> str | None:
    """Validate PDS path: must exist and have .pds extension."""
    if not pds_path:
        return None
    resolved = Path(pds_path).resolve()
    if not resolved.exists():
        return None
    if resolved.suffix.lower() != '.pds':
        return None
    return str(resolved)


@app.route("/api/pds-info", methods=["POST"])
def api_pds_info():
    """Quick PDS header analysis (no binary parsing)."""
    pds_path = _validate_pds_path(request.json.get("pds_path", ""))
    if not pds_path:
        return jsonify({"error": "Invalid or missing PDS file path"}), 400

    try:
        from pds_toolkit import read_pds_header
        meta = read_pds_header(pds_path, max_bytes=500_000)

        # Extract sensor offsets
        geom = meta.sections.get("GEOMETRY", {})
        sensors = {}
        for key, val in geom.items():
            if key.startswith("Offset("):
                parts = val.split(",")
                if len(parts) >= 4:
                    name = parts[0].strip()
                    try:
                        sensors[name] = {
                            "x": float(parts[1]), "y": float(parts[2]), "z": float(parts[3])
                        }
                    except ValueError:
                        pass

        # Extract calibration
        static_roll = static_pitch = 0.0
        apply_roll = apply_pitch = apply_heave = apply_svp = False
        svp_file = ""

        for sec_name, sec_data in meta.sections.items():
            for key, val in sec_data.items():
                kl = key.lower()
                if "staticroll" in kl:
                    for p in reversed(val.split(",")):
                        try:
                            static_roll = float(p.strip())
                            break
                        except ValueError:
                            logger.debug("Ignoring invalid static roll value: %s", p, exc_info=True)
                            continue
                elif "staticpitch" in kl:
                    for p in reversed(val.split(",")):
                        try:
                            static_pitch = float(p.strip())
                            break
                        except ValueError:
                            logger.debug("Ignoring invalid static pitch value: %s", p, exc_info=True)
                            continue
                elif "applyroll" in kl and "static" not in kl:
                    apply_roll = ",1" in val
                elif "applypitch" in kl and "static" not in kl:
                    apply_pitch = ",1" in val
                elif "applyheave" in kl:
                    apply_heave = ",1" in val
                elif "applysvp" in kl:
                    apply_svp = ",1" in val
                elif "svpfilename" in kl and "time" not in kl:
                    parts = val.split(",")
                    svp_file = parts[-1].strip() if parts else ""

        return jsonify({
            "vessel_name": meta.vessel_name,
            "survey_type": meta.survey_type,
            "pds_version": meta.pds_version,
            "file_version": meta.file_version,
            "coord_system": meta.coord_system_name,
            "start_time": meta.start_time,
            "sensors": sensors,
            "static_roll": static_roll,
            "static_pitch": static_pitch,
            "apply_roll": apply_roll,
            "apply_pitch": apply_pitch,
            "apply_heave": apply_heave,
            "apply_svp": apply_svp,
            "svp_file": svp_file,
            "sealevel": float(geom.get("Sealevel", "0") or "0"),
            "draft": float(geom.get("Draft", "0") or "0"),
            "file_size_mb": os.path.getsize(pds_path) / 1024 / 1024,
            "_pds_path": pds_path,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-offsets", methods=["POST"])
def api_verify_offsets():
    """Cross-validate PDS offsets against HVF and/or OffsetManager."""
    try:
        data = request.json
        pds_path = data.get("pds_path", "")
        hvf_dir = data.get("hvf_dir", "")
        om_config_id = data.get("om_config_id", "")
        project_id = data.get("project_id")
        offset_db_path = data.get("offset_db_path")
        allow_request_path = _flag_is_enabled(data.get("allow_sqlite_fallback"))

        if not os.path.isfile(pds_path):
            return jsonify({"error": "PDS file not found"}), 400

        # Read PDS header for offsets
        from pds_toolkit import read_pds_header
        meta = read_pds_header(pds_path)
        geom = meta.sections.get("GEOMETRY", {})

        pds_offsets = {}
        for key, val in geom.items():
            if key.startswith("Offset("):
                parts = val.split(",")
                if len(parts) >= 4:
                    name = parts[0].strip()
                    if name == "Zero Offset":
                        continue
                    pds_offsets[name] = {
                        "x": float(parts[1]),
                        "y": float(parts[2]),
                        "z": float(parts[3]),
                    }

        # PDS calibration values
        static_roll = 0.0
        static_pitch = 0.0
        for sec_data in meta.sections.values():
            for k, v in sec_data.items():
                if "StaticRoll" in k and "8193," in v:
                    try:
                        static_roll = float(v.split(",", 1)[1])
                    except (ValueError, IndexError):
                        pass
                if "StaticPitch" in k and "8193," in v:
                    try:
                        static_pitch = float(v.split(",", 1)[1])
                    except (ValueError, IndexError):
                        pass

        result = {
            "pds_offsets": pds_offsets,
            "static_roll": static_roll,
            "static_pitch": static_pitch,
            "hvf_comparison": None,
            "om_comparison": None,
            "om_lookup": None,
            "provenance": {"om": None},
            "issues": [],
        }

        # Compare with HVF if available
        if hvf_dir and os.path.isdir(hvf_dir):
            from pds_toolkit import read_hvf
            hvf_files = [f for f in os.listdir(hvf_dir) if f.endswith(".hvf")]
            if hvf_files:
                hvf_path = os.path.join(hvf_dir, hvf_files[0])
                try:
                    hvf = read_hvf(hvf_path)
                    hvf_data = {
                        "file": hvf_files[0],
                        "sensors": {},
                        "mount_roll": getattr(hvf, "mount_roll", 0.0),
                        "mount_pitch": getattr(hvf, "mount_pitch", 0.0),
                        "mount_heading": getattr(hvf, "mount_heading", 0.0),
                    }

                    # Extract HVF sensor offsets
                    if hasattr(hvf, "sensors"):
                        for s in hvf.sensors:
                            name = getattr(s, "name", "")
                            hvf_data["sensors"][name] = {
                                "x": getattr(s, "x", 0.0),
                                "y": getattr(s, "y", 0.0),
                                "z": getattr(s, "z", 0.0),
                            }

                    # Compare offsets
                    for sensor_name, pds_xyz in pds_offsets.items():
                        hvf_match = hvf_data["sensors"].get(sensor_name)
                        if hvf_match:
                            dx = abs(pds_xyz["x"] - hvf_match["x"])
                            dy = abs(pds_xyz["y"] - hvf_match["y"])
                            dz = abs(pds_xyz["z"] - hvf_match["z"])
                            if dx > 0.01 or dy > 0.01 or dz > 0.01:
                                result["issues"].append({
                                    "level": "WARN",
                                    "sensor": sensor_name,
                                    "message": f"Offset mismatch: PDS({pds_xyz['x']:.3f},{pds_xyz['y']:.3f},{pds_xyz['z']:.3f}) vs HVF({hvf_match['x']:.3f},{hvf_match['y']:.3f},{hvf_match['z']:.3f})",
                                    "delta": f"Δ=({dx:.3f},{dy:.3f},{dz:.3f})",
                                })

                    # Compare calibration
                    if abs(static_roll - hvf_data["mount_roll"]) > 0.01:
                        result["issues"].append({
                            "level": "WARN",
                            "sensor": "Calibration",
                            "message": f"Roll mismatch: PDS StaticRoll={static_roll:.4f}° vs HVF MountRoll={hvf_data['mount_roll']:.4f}°",
                            "delta": f"Δ={abs(static_roll - hvf_data['mount_roll']):.4f}°",
                        })
                    if abs(static_pitch - hvf_data["mount_pitch"]) > 0.01:
                        result["issues"].append({
                            "level": "WARN",
                            "sensor": "Calibration",
                            "message": f"Pitch mismatch: PDS StaticPitch={static_pitch:.4f}° vs HVF MountPitch={hvf_data['mount_pitch']:.4f}°",
                            "delta": f"Δ={abs(static_pitch - hvf_data['mount_pitch']):.4f}°",
                        })

                    result["hvf_comparison"] = hvf_data
                except Exception as e:
                    result["issues"].append({"level": "ERROR", "sensor": "HVF", "message": str(e), "delta": ""})

        # Compare with OffsetManager if available
        if om_config_id:
            om_lookup = _describe_om_resolution(
                offset_db_path,
                project_id=project_id,
                allow_request_path=allow_request_path,
            )
            result["om_lookup"] = om_lookup
            om_provenance = _build_om_provenance(om_lookup, om_lookup)
            result["provenance"]["om"] = om_provenance
            try:
                om_data, om_source = _load_om_config_detail(
                    int(om_config_id),
                    offset_db_path,
                    project_id=project_id,
                    allow_request_path=allow_request_path,
                    return_source=True,
                )
                om_provenance = _build_om_provenance(om_lookup, om_source)
                result["provenance"]["om"] = om_provenance
                comparison_source = om_source or {}
                if om_data:
                    config, _offsets = _normalize_om_detail(om_data)
                    result["om_comparison"] = {
                        "vessel": config.get("vessel_name", ""),
                        "project": config.get("project_name", ""),
                        "source": comparison_source.get("source", "unknown"),
                        "mode": comparison_source.get("mode", "api-first"),
                        "resolution": comparison_source,
                        "provenance": om_provenance,
                        "loaded": True,
                        "semantic_state": om_provenance["semantic"]["state"],
                        "semantic_hint": om_provenance["semantic"]["hint"],
                        "semantic_checks": om_provenance["semantic"]["checks"],
                        "schema_version": om_provenance["semantic"]["schema_version"],
                        "schema_ok": om_provenance["semantic"]["schema_ok"],
                        "stale_risk": om_provenance["semantic"]["stale_risk"],
                        "latest_vessel_name": om_provenance["semantic"]["latest_vessel_name"],
                        "latest_project_name": om_provenance["semantic"]["latest_project_name"],
                        "latest_config_date": om_provenance["semantic"]["latest_config_date"],
                        "latest_updated_at": om_provenance["semantic"]["latest_updated_at"],
                        "latest_review_state": om_provenance["semantic"]["latest_review_state"],
                    }
                    result["om_comparison"]["fallback_source"] = om_lookup.get("source", "unresolved")
                    result["om_comparison"]["fallback_mode"] = om_lookup.get("mode", "api-first")
                    result["om_comparison"]["fallback_resolution"] = om_lookup
                else:
                    lookup_path = om_lookup.get("path", "")
                    lookup_hint = om_lookup.get("hint", "")
                    semantic_state = om_lookup.get("semantic_state", "")
                    semantic_hint = om_lookup.get("semantic_hint", "")
                    result["om_comparison"] = {
                        "vessel": "",
                        "project": "",
                        "source": comparison_source.get("source", "unknown"),
                        "mode": comparison_source.get("mode", "api-first"),
                        "resolution": comparison_source,
                        "provenance": om_provenance,
                        "loaded": False,
                        "semantic_state": om_provenance["semantic"]["state"],
                        "semantic_hint": om_provenance["semantic"]["hint"],
                        "semantic_checks": om_provenance["semantic"]["checks"],
                        "schema_version": om_provenance["semantic"]["schema_version"],
                        "schema_ok": om_provenance["semantic"]["schema_ok"],
                        "stale_risk": om_provenance["semantic"]["stale_risk"],
                        "latest_vessel_name": om_provenance["semantic"]["latest_vessel_name"],
                        "latest_project_name": om_provenance["semantic"]["latest_project_name"],
                        "latest_config_date": om_provenance["semantic"]["latest_config_date"],
                        "latest_updated_at": om_provenance["semantic"]["latest_updated_at"],
                        "latest_review_state": om_provenance["semantic"]["latest_review_state"],
                    }
                    result["om_comparison"]["fallback_source"] = om_lookup.get("source", "unresolved")
                    result["om_comparison"]["fallback_mode"] = om_lookup.get("mode", "api-first")
                    result["om_comparison"]["fallback_resolution"] = om_lookup
                    if om_lookup.get("source") == "unresolved":
                        if semantic_state in {"stale-risk", "schema-risk", "missing", "empty"}:
                            message = "프로젝트 저장 DB fallback이 semantic 검사에서 차단되어 비교를 건너뛰었습니다."
                            delta = semantic_hint or lookup_hint or "프로젝트 저장 DB가 오래된 복사본이거나 스키마가 맞지 않을 수 있습니다."
                        else:
                            message = "OffsetManager API를 우선 조회했지만, 명시적 SQLite fallback이 비활성화되어 비교를 건너뛰었습니다."
                            delta = lookup_hint or "프로젝트 저장 경로 또는 '명시적 SQLite fallback 사용' 체크 상태를 확인해 주세요."
                    else:
                        message = "OffsetManager 설정을 읽지 못했습니다. API 연결 상태 또는 '명시적 SQLite fallback 사용' 체크 상태를 점검해 주세요."
                        delta = lookup_path or lookup_hint or "DB 경로 확인 필요"
                    result["issues"].append({
                        "level": "WARN",
                        "sensor": "OffsetManager",
                        "message": message,
                        "delta": delta,
                        "provenance": {
                            "source": om_provenance["decision"].get("source", "unknown"),
                            "mode": om_provenance["decision"].get("mode", "api-first"),
                            "fallback_scope": om_lookup.get("fallback_scope", "none"),
                            "semantic_state": om_provenance["semantic"].get("state", ""),
                            "request_fallback_enabled": om_provenance["decision"].get("request_fallback_enabled", False),
                            "project_fallback_enabled": om_provenance["decision"].get("project_fallback_enabled", False),
                        },
                    })
            except Exception:
                lookup_path = result["om_lookup"].get("path", "") if result["om_lookup"] else ""
                lookup_hint = result["om_lookup"].get("hint", "") if result["om_lookup"] else ""
                result["issues"].append({
                    "level": "WARN",
                    "sensor": "OffsetManager",
                    "message": "OffsetManager 연동 중 예외가 발생했습니다. API 우선 흐름과 '명시적 SQLite fallback 사용' 체크 상태를 다시 확인해 주세요.",
                    "delta": lookup_path or lookup_hint or "DB 경로 확인 필요",
                })

        if not result["issues"]:
            result["issues"].append({"level": "PASS", "sensor": "All", "message": "No offset discrepancies found", "delta": ""})

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/verify-motion", methods=["POST"])
def api_verify_motion():
    """Analyze motion data from PDS for spikes, gaps, and drift."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        max_pings = data.get("max_pings", 20)

        if not os.path.isfile(pds_path):
            return jsonify({"error": "PDS file not found"}), 400

        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(pds_path, max_pings=max_pings)

        result = {
            "nav": {"total": 0, "gaps": [], "speed": {}},
            "attitude": {"total": 0, "roll": {}, "pitch": {}, "heave": {}, "heading": {}, "spikes": []},
            "tide": {"total": 0, "range": {}},
            "ping": {"total": 0, "rate_hz": 0, "depth_range": []},
            "issues": [],
        }

        # Navigation analysis
        if pds.navigation:
            nav = pds.navigation
            result["nav"]["total"] = len(nav)
            lats = [r.latitude for r in nav if r.latitude != 0]
            lons = [r.longitude for r in nav if r.longitude != 0]
            if lats:
                result["nav"]["lat_range"] = [min(lats), max(lats)]
                result["nav"]["lon_range"] = [min(lons), max(lons)]

            # Speed from consecutive positions
            speeds = []
            for i in range(1, len(nav)):
                dt = (nav[i].timestamp - nav[i-1].timestamp) / 1000.0
                if dt > 0 and dt < 10:
                    dlat = (nav[i].latitude - nav[i-1].latitude) * 111320
                    dlon = (nav[i].longitude - nav[i-1].longitude) * 111320 * np.cos(np.radians(nav[i].latitude))
                    dist = np.sqrt(dlat**2 + dlon**2)
                    speeds.append(dist / dt)
            if speeds:
                result["nav"]["speed"] = {
                    "min": round(min(speeds), 2),
                    "max": round(max(speeds), 2),
                    "mean": round(np.mean(speeds), 2),
                    "knots_mean": round(np.mean(speeds) * 1.944, 2),
                }

            # Gap detection
            for i in range(1, len(nav)):
                dt = (nav[i].timestamp - nav[i-1].timestamp) / 1000.0
                if dt > 5.0:  # > 5 seconds = gap
                    result["nav"]["gaps"].append({
                        "index": i,
                        "duration_sec": round(dt, 1),
                    })

            if result["nav"]["gaps"]:
                result["issues"].append({
                    "level": "WARN",
                    "category": "Navigation",
                    "message": f"{len(result['nav']['gaps'])} navigation gap(s) detected (>{5}s)",
                })

        # Attitude analysis
        if pds.attitude:
            att = pds.attitude
            result["attitude"]["total"] = len(att)

            rolls = [a.roll for a in att if hasattr(a, 'roll')]
            pitches = [a.pitch for a in att if hasattr(a, 'pitch')]
            heaves = [a.heave for a in att if hasattr(a, 'heave')]
            headings = [a.heading for a in att if hasattr(a, 'heading')]

            def _stats(vals, name):
                if not vals:
                    return {}
                arr = np.array(vals)
                s = {
                    "min": round(float(arr.min()), 4),
                    "max": round(float(arr.max()), 4),
                    "mean": round(float(arr.mean()), 4),
                    "std": round(float(arr.std()), 4),
                }

                # Spike detection: values > 3*std from mean
                mean, std = arr.mean(), arr.std()
                if std > 0:
                    spikes_idx = np.where(np.abs(arr - mean) > 3 * std)[0]
                    s["spike_count"] = int(len(spikes_idx))
                    if len(spikes_idx) > 0:
                        for si in spikes_idx[:5]:
                            result["attitude"]["spikes"].append({
                                "channel": name,
                                "index": int(si),
                                "value": round(float(arr[si]), 4),
                                "threshold": round(float(mean + 3 * std), 4),
                            })
                else:
                    s["spike_count"] = 0

                # Drift detection: linear trend
                if len(arr) > 10:
                    x = np.arange(len(arr))
                    coeffs = np.polyfit(x, arr, 1)
                    s["drift_rate"] = round(float(coeffs[0]) * len(arr), 4)  # total drift over period
                    if abs(s["drift_rate"]) > 0.5:  # > 0.5° or 0.5m drift
                        result["issues"].append({
                            "level": "WARN",
                            "category": "Attitude",
                            "message": f"{name} drift detected: {s['drift_rate']:.4f}° over {len(arr)} samples",
                        })
                return s

            result["attitude"]["roll"] = _stats(rolls, "Roll")
            result["attitude"]["pitch"] = _stats(pitches, "Pitch")
            result["attitude"]["heave"] = _stats(heaves, "Heave")
            result["attitude"]["heading"] = _stats(headings, "Heading")

            # Report spikes
            total_spikes = sum(result["attitude"][ch].get("spike_count", 0) for ch in ["roll", "pitch", "heave"])
            if total_spikes > 0:
                result["issues"].append({
                    "level": "WARN",
                    "category": "Attitude",
                    "message": f"{total_spikes} attitude spike(s) detected (>3σ)",
                })

        # Tide analysis
        if hasattr(pds, 'tide') and pds.tide:
            tide = pds.tide
            result["tide"]["total"] = len(tide)
            values = [t.value for t in tide if hasattr(t, 'value')]
            if values:
                result["tide"]["range"] = {
                    "min": round(min(values), 3),
                    "max": round(max(values), 3),
                    "mean": round(np.mean(values), 3),
                }
        elif hasattr(pds, 'num_tide_records'):
            result["tide"]["total"] = pds.num_tide_records

        # Ping summary
        valid_pings = [p for p in pds.pings if len(p.depth) > 0 and np.any(p.depth != 0)]
        result["ping"]["total"] = len(valid_pings)
        result["ping"]["rate_hz"] = round(pds.ping_rate_hz, 2) if pds.ping_rate_hz else 0

        if valid_pings:
            all_depths = np.concatenate([np.abs(p.depth[p.depth != 0]) for p in valid_pings])
            if len(all_depths) > 0:
                result["ping"]["depth_range"] = [round(float(all_depths.min()), 2), round(float(all_depths.max()), 2)]

        if not result["issues"]:
            result["issues"].append({
                "level": "PASS",
                "category": "All",
                "message": "No motion anomalies detected",
            })

        return jsonify(result)

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/nav-track", methods=["POST"])
def api_nav_track():
    """Get navigation track as GeoJSON for map display."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        max_pings = data.get("max_pings", 50)

        if not os.path.isfile(pds_path):
            return jsonify({"error": "PDS file not found"}), 400

        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(pds_path, max_pings=max_pings)

        coords = []
        timestamps = []
        for r in pds.navigation:
            if r.latitude != 0 and r.longitude != 0:
                coords.append([r.longitude, r.latitude])
                if r.timestamp > 0:
                    timestamps.append(r.timestamp)

        if not coords:
            return jsonify({"error": "No navigation data found"}), 404

        # Ping positions (from depth array pings with timestamps)
        ping_points = []
        for p in pds.pings:
            if p.datetime_utc and len(p.depth) > 0:
                # Find nearest nav point
                for r in pds.navigation:
                    if r.latitude != 0 and abs(r.timestamp - p.timestamp) < 2000:
                        nadir_depth = float(np.abs(p.depth[p.depth != 0]).mean()) if np.any(p.depth != 0) else 0
                        ping_points.append({
                            "lon": r.longitude,
                            "lat": r.latitude,
                            "depth": round(nadir_depth, 1),
                            "time": str(p.datetime_utc)[:19],
                        })
                        break

        # Build GeoJSON
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "name": os.path.basename(pds_path),
                        "points": len(coords),
                    },
                }
            ],
            "ping_points": ping_points,
            "bounds": {
                "south": min(c[1] for c in coords),
                "north": max(c[1] for c in coords),
                "west": min(c[0] for c in coords),
                "east": max(c[0] for c in coords),
            },
        }

        return jsonify(geojson)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/beam-profile", methods=["POST"])
def api_beam_profile():
    """Get beam depth profile (across-track vs depth) for a single ping."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        ping_index = data.get("ping_index", 0)
        source = data.get("source", "pds")  # "pds" or "gsf"

        if not os.path.isfile(pds_path):
            return jsonify({"error": "File not found"}), 400

        if source == "pds":
            from pds_toolkit import read_pds_binary
            pds = read_pds_binary(pds_path, max_pings=ping_index + 3)
            if ping_index >= len(pds.pings):
                return jsonify({"error": f"Ping {ping_index} not found"}), 404

            p = pds.pings[ping_index]
            across = p.across_track if len(p.across_track) > 0 else np.zeros(1024)
            depth = np.abs(p.depth) if len(p.depth) > 0 else np.zeros(1024)

            # Filter valid beams
            valid = (depth > 0.1) & np.isfinite(across) & np.isfinite(depth)
            result = {
                "across": [round(float(x), 2) for x in across[valid]],
                "depth": [round(float(d), 2) for d in depth[valid]],
                "num_beams": int(valid.sum()),
                "nadir_depth": round(float(depth[len(depth)//2]), 2) if len(depth) > 0 else 0,
                "swath_width": round(float(across[valid].max() - across[valid].min()), 1) if valid.sum() > 0 else 0,
                "ping_index": ping_index,
                "source": source,
                "time": str(p.datetime_utc)[:19] if p.datetime_utc else "",
            }
        else:
            return jsonify({"error": "GSF source not yet implemented"}), 501

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/attitude-timeseries", methods=["POST"])
def api_attitude_timeseries():
    """Get attitude time series data for charting."""
    try:
        import numpy as np
        data = request.json
        pds_path = data.get("pds_path", "")
        max_pings = data.get("max_pings", 50)

        if not os.path.isfile(pds_path):
            return jsonify({"error": "File not found"}), 400

        from pds_toolkit import read_pds_binary
        pds = read_pds_binary(pds_path, max_pings=max_pings)

        result = {"roll": [], "pitch": [], "heave": [], "heading": [], "timestamps": []}

        for a in pds.attitude:
            t = getattr(a, 'timestamp', 0)
            if t > 0:
                result["timestamps"].append(round(t / 1000.0, 3))  # seconds since epoch
            result["roll"].append(round(getattr(a, 'roll', 0), 4))
            result["pitch"].append(round(getattr(a, 'pitch', 0), 4))
            result["heave"].append(round(getattr(a, 'heave', 0), 4))
            result["heading"].append(round(getattr(a, 'heading', 0), 2))

        result["total"] = len(pds.attitude)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/line-stats", methods=["POST"])
def api_line_stats():
    """Get per-line depth statistics from GSF files."""
    try:
        import numpy as np
        data = request.json
        gsf_dir = data.get("gsf_dir", "")
        max_pings = data.get("max_pings", 100)

        if not os.path.isdir(gsf_dir):
            return jsonify({"error": "GSF directory not found"}), 400

        from pds_toolkit import read_gsf
        gsf_files = sorted([f for f in os.listdir(gsf_dir) if f.endswith('.gsf')])

        lines = []
        for gf in gsf_files[:20]:  # max 20 lines
            try:
                gsf_path = os.path.join(gsf_dir, gf)
                gsf = read_gsf(gsf_path, max_pings=max_pings)
                if not gsf.pings:
                    continue

                all_depths = []
                for p in gsf.pings:
                    if hasattr(p, 'depth') and len(p.depth) > 0:
                        valid = p.depth[p.depth > 0.5]
                        if len(valid) > 0:
                            all_depths.extend(valid.tolist())

                if all_depths:
                    arr = np.array(all_depths)
                    lines.append({
                        "name": gf,
                        "pings": len(gsf.pings),
                        "beams": len(all_depths),
                        "depth_min": round(float(arr.min()), 2),
                        "depth_max": round(float(arr.max()), 2),
                        "depth_mean": round(float(arr.mean()), 2),
                        "depth_std": round(float(arr.std()), 2),
                    })
            except Exception:
                continue

        # Flag outlier lines (depth mean > 2σ from overall mean)
        if len(lines) > 2:
            means = np.array([l["depth_mean"] for l in lines])
            overall_mean = float(means.mean())
            overall_std = float(means.std())
            for l in lines:
                diff = abs(l["depth_mean"] - overall_mean)
                l["outlier"] = diff > 2 * overall_std if overall_std > 0 else False

        return jsonify({"lines": lines, "total": len(lines)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/crossline-compare", methods=["POST"])
def api_crossline_compare():
    """Compare depth at cross-line intersection points."""
    try:
        import numpy as np
        data = request.json
        gsf_dir = data.get("gsf_dir", "")
        max_pings = data.get("max_pings", 100)
        cell_size = data.get("cell_size", 5.0)  # grid cell for intersection

        if not os.path.isdir(gsf_dir):
            return jsonify({"error": "GSF 디렉토리를 찾을 수 없습니다"}), 400

        from pds_toolkit import read_gsf
        gsf_files = sorted([f for f in os.listdir(gsf_dir) if f.endswith('.gsf')])

        if len(gsf_files) < 2:
            return jsonify({"error": "교차 비교에 최소 2개 GSF 라인이 필요합니다"}), 400

        # Load all lines with beam positions
        lines = []
        for gf in gsf_files[:30]:
            try:
                gsf_path = os.path.join(gsf_dir, gf)
                gsf = read_gsf(gsf_path, max_pings=max_pings)
                if not gsf.pings:
                    continue

                line_data = {"name": gf, "cells": {}}

                for p in gsf.pings:
                    lat = getattr(p, 'latitude', 0)
                    lon = getattr(p, 'longitude', 0)
                    if lat == 0 or lon == 0:
                        continue
                    if not hasattr(p, 'depth') or len(p.depth) == 0:
                        continue

                    # Nadir depth
                    nadir_idx = len(p.depth) // 2
                    depth = float(abs(p.depth[nadir_idx]))
                    if depth < 0.5:
                        continue

                    # Grid cell key
                    cell_x = int(lon / (cell_size / 111320))
                    cell_y = int(lat / (cell_size / 111320))
                    key = f"{cell_x},{cell_y}"

                    if key not in line_data["cells"]:
                        line_data["cells"][key] = []
                    line_data["cells"][key].append(depth)

                if line_data["cells"]:
                    # Average depth per cell
                    line_data["cell_means"] = {
                        k: float(np.mean(v)) for k, v in line_data["cells"].items()
                    }
                    lines.append(line_data)
            except Exception:
                continue

        # Find intersections (cells that appear in 2+ lines)
        intersections = []
        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                common_cells = set(lines[i]["cell_means"].keys()) & set(lines[j]["cell_means"].keys())
                for cell in common_cells:
                    d1 = lines[i]["cell_means"][cell]
                    d2 = lines[j]["cell_means"][cell]
                    diff = d1 - d2
                    intersections.append({
                        "line1": lines[i]["name"],
                        "line2": lines[j]["name"],
                        "cell": cell,
                        "depth1": round(d1, 3),
                        "depth2": round(d2, 3),
                        "diff": round(diff, 3),
                        "abs_diff": round(abs(diff), 3),
                    })

        if not intersections:
            return jsonify({
                "intersections": [],
                "stats": None,
                "iho": None,
                "message": "교차점이 발견되지 않았습니다. 셀 크기를 늘려보세요.",
            })

        # Statistics
        diffs = np.array([x["abs_diff"] for x in intersections])
        stats = {
            "num_intersections": len(intersections),
            "mean_diff": round(float(diffs.mean()), 4),
            "std_diff": round(float(diffs.std()), 4),
            "max_diff": round(float(diffs.max()), 4),
            "rms_diff": round(float(np.sqrt(np.mean(diffs ** 2))), 4),
            "p95_diff": round(float(np.percentile(diffs, 95)), 4),
        }

        # IHO S-44 Assessment
        # Use mean depth for IHO calculation
        mean_depth = float(np.mean([x["depth1"] for x in intersections]))

        iho_orders = {
            "Special": {"a": 0.25, "b": 0.0075},
            "1a": {"a": 0.5, "b": 0.013},
            "1b": {"a": 0.5, "b": 0.013},
            "2": {"a": 1.0, "b": 0.023},
        }

        iho_results = {}
        for order, params in iho_orders.items():
            tvu = np.sqrt(params["a"] ** 2 + (params["b"] * mean_depth) ** 2)
            passed = stats["p95_diff"] <= tvu * 2  # 95% CI
            iho_results[order] = {
                "tvu_limit": round(tvu, 3),
                "passed": passed,
                "margin": round(tvu * 2 - stats["p95_diff"], 3),
            }

        # Best passing order
        best_order = "불합격"
        for order in ["Special", "1a", "1b", "2"]:
            if iho_results[order]["passed"]:
                best_order = f"IHO {order}"
                break

        result = {
            "intersections": sorted(intersections, key=lambda x: -x["abs_diff"])[:50],
            "stats": stats,
            "iho": iho_results,
            "best_order": best_order,
            "mean_depth": round(mean_depth, 1),
            "num_lines": len(lines),
        }

        return jsonify(result)

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/om-configs")
def api_om_configs():
    """List OffsetManager vessel configs."""
    include_provenance = request.args.get("include_provenance", "").lower() in {"1", "true", "yes"}
    allow_request_path = _flag_is_enabled(request.args.get("allow_sqlite_fallback"))
    configs_result = _load_om_configs(
        request.args.get("db_path"),
        request.args.get("project_id"),
        allow_request_path=allow_request_path,
        return_source=include_provenance,
    )
    if include_provenance:
        configs, provenance = configs_result
        return jsonify({
            "configs": configs,
            "count": len(configs),
            "provenance": provenance,
        })
    return jsonify(configs_result)


@app.route("/api/om-sensors/<int:config_id>")
def api_om_sensors(config_id):
    """Get sensor offsets for a specific OffsetManager config."""
    try:
        allow_request_path = _flag_is_enabled(request.args.get("allow_sqlite_fallback"))
        detail_result = _load_om_config_detail(
            config_id,
            request.args.get("db_path"),
            request.args.get("project_id"),
            allow_request_path=allow_request_path,
            return_source=True,
        )
        detail, om_source = detail_result if detail_result else (None, _describe_om_resolution(
            request.args.get("db_path"),
            request.args.get("project_id"),
            allow_request_path=allow_request_path,
        ))
        if not detail:
            resolution = _describe_om_resolution(
                request.args.get("db_path"),
                request.args.get("project_id"),
                allow_request_path=allow_request_path,
            )
            return jsonify({
                "error": "Config not found",
                "detail": resolution.get("hint") or "OffsetManager API와 명시적 SQLite fallback 경로를 모두 확인하지 못했습니다. 프로젝트의 OM 연결 상태와 '명시적 SQLite fallback 사용' 체크 상태를 점검해 주세요.",
            }), 404

        config, sensors = _normalize_om_detail(detail)
        source_label = om_source.get("source_label", "OffsetManager API")

        # Sensor type Korean labels
        type_labels = {
            "MRU": "모션센서 (MRU)",
            "IMU": "관성센서 (IMU)",
            "MBES Transducer": "멀티빔 (MBES)",
            "GPS": "GNSS 안테나",
            "SBP Transducer": "SBP 트랜스듀서",
            "Echosounder": "싱글빔",
            "Towpoint": "견인점",
            "Other": "기타",
        }

        result = {
            "config": {
                "id": config.get("id"),
                "vessel_name": config.get("vessel_name", ""),
                "project_name": config.get("project_name", ""),
                "config_date": config.get("config_date", ""),
                "reference_point": config.get("reference_point", ""),
                "description": config.get("description", ""),
            },
            "sensors": [],
            "total": len(sensors),
            "source": om_source.get("source", "unknown"),
            "source_label": source_label,
            "mode": om_source.get("mode", "api-first"),
            "path": om_source.get("path", ""),
            "exists": om_source.get("exists", False),
            "hint": om_source.get("hint", ""),
            "resolution": om_source,
            "provenance": {
                "om": _build_om_provenance(om_source, om_source),
            },
        }

        for s in sensors:
            if isinstance(s, dict):
                sensor_id = s.get("id")
                sensor_name = s.get("sensor_name", s.get("name", ""))
                sensor_type = s.get("sensor_type", s.get("type", ""))
                x_offset = s.get("x_offset", s.get("x", 0))
                y_offset = s.get("y_offset", s.get("y", 0))
                z_offset = s.get("z_offset", s.get("z", 0))
                roll_offset = s.get("roll_offset", s.get("roll", 0))
                pitch_offset = s.get("pitch_offset", s.get("pitch", 0))
                heading_offset = s.get("heading_offset", s.get("heading", 0))
                latency = s.get("latency", 0)
                notes = s.get("notes", "")
            else:
                sensor_id = getattr(s, "id", None)
                sensor_name = getattr(s, "sensor_name", "")
                sensor_type = getattr(s, "sensor_type", "")
                x_offset = getattr(s, "x_offset", 0)
                y_offset = getattr(s, "y_offset", 0)
                z_offset = getattr(s, "z_offset", 0)
                roll_offset = getattr(s, "roll_offset", 0)
                pitch_offset = getattr(s, "pitch_offset", 0)
                heading_offset = getattr(s, "heading_offset", 0)
                latency = getattr(s, "latency", 0)
                notes = getattr(s, "notes", "")

            result["sensors"].append({
                "id": sensor_id,
                "name": sensor_name,
                "type": sensor_type,
                "type_ko": type_labels.get(sensor_type, sensor_type),
                "x": float(x_offset or 0),
                "y": float(y_offset or 0),
                "z": float(z_offset or 0),
                "roll": float(roll_offset or 0),
                "pitch": float(pitch_offset or 0),
                "heading": float(heading_offset or 0),
                "latency": float(latency or 0),
                "notes": notes or "",
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan-folders", methods=["POST"])
def api_scan_folders():
    """Scan companion folders for matching files by timestamp."""
    data = request.json or {}
    pds_path = _validate_pds_path(data.get("pds_path", ""))
    if not pds_path:
        return jsonify({"error": "Invalid PDS path"}), 400

    # Extract timestamp from PDS filename
    stem = Path(pds_path).stem
    import re
    ts_match = re.search(r'(\d{8})-?(\d{6})', stem)
    pds_ts = f"{ts_match.group(1)}-{ts_match.group(2)}" if ts_match else ""

    found = {}
    for folder_key in ["gsf_dir", "hvf_dir", "s7k_dir", "fau_dir"]:
        folder = data.get(folder_key, "")
        if not folder or not Path(folder).is_dir():
            continue

        ext_map = {
            "gsf_dir": [".gsf"],
            "hvf_dir": [".hvf"],
            "s7k_dir": [".s7k"],
            "fau_dir": [".fau"],
        }
        exts = ext_map.get(folder_key, [])
        matches = []
        for f in Path(folder).iterdir():
            if f.suffix.lower() in exts:
                # Check timestamp match
                f_ts = re.search(r'(\d{8})-?(\d{6})', f.stem)
                matched = False
                if pds_ts and f_ts:
                    matched = f_ts.group(1) == ts_match.group(1)  # same date
                matches.append({
                    "name": f.name,
                    "size_mb": f.stat().st_size / 1024 / 1024,
                    "matched": matched,
                })
        if matches:
            found[folder_key] = sorted(matches, key=lambda x: x["name"])

    return jsonify({"pds_timestamp": pds_ts, "found": found})


@app.route("/api/run-qc", methods=["POST"])
def api_run_qc():
    """Start QC job in background thread."""
    data = request.json or {}
    pds_path = _validate_pds_path(data.get("pds_path", ""))

    if not pds_path:
        return jsonify({"error": "Invalid or missing PDS file path"}), 400

    job_id = _next_id('job')
    job = {
        "id": job_id,
        "pds_path": pds_path,
        "gsf_dir": data.get("gsf_dir", ""),
        "hvf_dir": data.get("hvf_dir", ""),
        "om_config_id": data.get("om_config_id"),
        "lat_range": data.get("lat_range", [-90, 90]),
        "lon_range": data.get("lon_range", [-180, 180]),
        "max_pings": data.get("max_pings"),
        "cell_size": data.get("cell_size", 5.0),
        "status": "running",
        "progress": "Starting...",
        "started_at": datetime.now().isoformat(),
        "result": None,
    }
    _jobs[job_id] = job

    thread = threading.Thread(target=_run_qc_job, args=(job,), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "started"})


@app.route("/api/project-qc/<int:project_id>/<int:file_idx>", methods=["POST"])
def api_project_qc(project_id, file_idx):
    """Run QC for a specific PDS file within a project."""
    project = _projects.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    if file_idx >= len(project["pds_files"]):
        return jsonify({"error": "File index out of range"}), 400

    pds_info = project["pds_files"][file_idx]
    pds_path = pds_info["path"]
    if not Path(pds_path).exists():
        return jsonify({"error": f"File not found: {pds_path}"}), 404

    job_id = _next_id('job')
    job = {
        "id": job_id,
        "project_id": project_id,
        "pds_path": pds_path,
        "gsf_dir": project.get("gsf_dir", ""),
        "hvf_dir": project.get("hvf_dir", ""),
        "om_config_id": project.get("om_config_id"),
        "lat_range": [-90, 90],
        "lon_range": [-180, 180],
        "max_pings": None,
        "cell_size": 5.0,
        "status": "running",
        "progress": "Starting...",
        "started_at": datetime.now().isoformat(),
        "result": None,
    }
    _jobs[job_id] = job
    project.setdefault("jobs", []).append(job_id)

    thread = threading.Thread(target=_run_qc_job, args=(job,), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "started"})


@app.route("/api/status/<int:job_id>")
def api_status(job_id):
    """Poll job status."""
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    result = job.get("result") if job.get("status") == "done" else None
    provenance_summary = _extract_job_provenance_summary(result)
    provenance_manifest = _extract_job_provenance_manifest(result)
    response = {
        "id": job_id,
        "status": job["status"],
        "progress": job.get("progress", ""),
    }
    if provenance_summary:
        response["provenance_summary"] = provenance_summary
    if provenance_manifest:
        response["provenance_manifest"] = provenance_manifest
    return jsonify(response)


@app.route("/api/download/<int:job_id>/<fmt>")
def api_download(job_id, fmt):
    """Download generated report."""
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404

    if fmt == "json":
        payload = _build_job_download_payload(job)
        if not payload:
            return jsonify({"error": "Not found"}), 404
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return send_file(
            BytesIO(data),
            mimetype="application/json",
            as_attachment=True,
            download_name=f"QC_Result_{job_id}.json",
        )

    if not job.get("output_dir"):
        return jsonify({"error": "Not found"}), 404

    out = Path(job["output_dir"])
    file_map = {
        "excel": out / "QC_Report.xlsx",
        "word": out / "QC_Report.docx",
        "ppt": out / "DQR_MBES.pptx",
    }

    path = file_map.get(fmt)
    if path and path.exists():
        return send_file(str(path), as_attachment=True)

    # Try generating report on-demand if result exists
    if job.get("result") and fmt in file_map:
        try:
            from mbes_qc.pds_qc import run_pds_qc
            os.makedirs(str(out), exist_ok=True)

            if fmt == "excel":
                from mbes_qc.report import generate_excel_report
                generate_excel_report(job["result"], str(file_map["excel"]))
            elif fmt == "word":
                from mbes_qc.report import generate_word_report
                generate_word_report(job["result"], str(file_map["word"]))
            elif fmt == "ppt":
                from mbes_qc.dqr_ppt import generate_dqr_ppt
                generate_dqr_ppt(job["result"], str(file_map["ppt"]))

            if file_map[fmt].exists():
                return send_file(str(file_map[fmt]), as_attachment=True)
        except Exception as e:
            return jsonify({"error": f"Report generation failed: {e}"}), 500

    return jsonify({"error": f"파일을 찾을 수 없습니다: {fmt}"}), 404


# ── Background QC Job ──────────────────────────────────────

def _run_qc_job(job: dict) -> None:
    """Execute QC in background thread."""
    try:
        from mbes_qc.pds_qc import run_pds_qc

        pds_path = job["pds_path"]
        output_dir = str(Path(pds_path).parent / f"QC_{Path(pds_path).stem}")

        job["progress"] = "Running QC pipeline..."
        job["output_dir"] = output_dir

        result = run_pds_qc(
            pds_path=pds_path,
            gsf_dir=job.get("gsf_dir") or None,
            hvf_dir=job.get("hvf_dir") or None,
            output_dir=output_dir,
            max_pings=job.get("max_pings"),
            lat_range=tuple(job.get("lat_range", (-90, 90))),
            lon_range=tuple(job.get("lon_range", (-180, 180))),
            cell_size=job.get("cell_size", 5.0),
            generate_reports=False,  # reports generated on-demand via download API
        )

        # Serialize result for template
        serialized = _serialize_result(result)
        with _job_lock:
            job["result"] = serialized
            job["finished_at"] = datetime.now().isoformat()
            job["progress"] = "Complete"
            job["status"] = "done"  # set LAST so status poll sees complete state

    except Exception as e:
        job["status"] = "error"
        job["progress"] = f"Error: {e}"
        job["finished_at"] = datetime.now().isoformat()
        import traceback
        job["traceback"] = traceback.format_exc()


def _safe_float(v):
    """Convert numpy/nan/inf to JSON-safe Python float."""
    import math
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return round(f, 6)
    except (TypeError, ValueError):
        return 0.0


def _serialize_result(result) -> dict:
    """Convert PdsQcResult to JSON-safe dict."""
    d = {
        "vessel_name": result.vessel_name,
        "total_pings": int(result.total_pings),
        "total_beams": int(result.total_beams),
        "depth_range": [_safe_float(x) for x in result.depth_range],
        "lat_range": [_safe_float(x) for x in result.lat_range],
        "lon_range": [_safe_float(x) for x in result.lon_range],
        "nav_records": int(result.nav_records),
        "attitude_records": int(result.attitude_records),
        "elapsed_sec": _safe_float(result.elapsed_sec),
    }

    # Pre-processing checks
    if result.preprocess:
        d["preprocess"] = {
            "overall": result.preprocess.overall,
            "num_pass": result.preprocess.num_pass,
            "num_warn": result.preprocess.num_warn,
            "num_fail": result.preprocess.num_fail,
            "checks": [
                {
                    "category": c.category,
                    "name": c.name,
                    "status": c.status,
                    "pds_value": c.pds_value,
                    "reference_value": c.reference_value,
                    "suggestion": c.suggestion,
                }
                for c in result.preprocess.checks
            ],
        }

    # Swath lines summary
    if result.swath_lines:
        d["lines"] = {
            "num_lines": len(result.swath_lines),
            "details": [],
        }
        for sw in result.swath_lines:
            d["lines"]["details"].append({
                "name": sw.line_name,
                "format": sw.source_format,
                "pings": int(sw.num_pings),
                "heading": _safe_float(sw.mean_heading),
                "depth": _safe_float(sw.mean_depth),
                "duration": _safe_float(sw.duration_seconds),
            })

    # Motion QC summary
    if hasattr(result, 'motion_qc') and result.motion_qc:
        mq = result.motion_qc
        d["motion"] = {
            "roll_mean": _safe_float(getattr(mq, 'roll_mean', 0)),
            "roll_std": _safe_float(getattr(mq, 'roll_std', 0)),
            "roll_min": _safe_float(getattr(mq, 'roll_min', 0)),
            "roll_max": _safe_float(getattr(mq, 'roll_max', 0)),
            "pitch_mean": _safe_float(getattr(mq, 'pitch_mean', 0)),
            "pitch_std": _safe_float(getattr(mq, 'pitch_std', 0)),
            "pitch_min": _safe_float(getattr(mq, 'pitch_min', 0)),
            "pitch_max": _safe_float(getattr(mq, 'pitch_max', 0)),
            "heave_mean": _safe_float(getattr(mq, 'heave_mean', 0)),
            "heave_std": _safe_float(getattr(mq, 'heave_std', 0)),
            "heave_min": _safe_float(getattr(mq, 'heave_min', 0)),
            "heave_max": _safe_float(getattr(mq, 'heave_max', 0)),
            "heading_mean": _safe_float(getattr(mq, 'heading_mean', 0)),
            "attitude_records": int(getattr(mq, 'total_samples', 0)),
            "roll_spikes": int(getattr(mq, 'roll_spikes', 0)),
            "pitch_spikes": int(getattr(mq, 'pitch_spikes', 0)),
            "heave_spikes": int(getattr(mq, 'heave_spikes', 0)),
        }

    # Beam stats
    d["beam_stats"] = {
        "total_pings": int(result.total_pings),
        "beams_per_ping": int(getattr(result, 'beams_per_ping', 0)),
        "depth_min": _safe_float(result.depth_range[0]) if result.depth_range else 0,
        "depth_max": _safe_float(result.depth_range[1]) if len(result.depth_range) > 1 else 0,
    }

    # Offset QC
    if hasattr(result, 'offset_qc') and result.offset_qc:
        oq = result.offset_qc
        d["offset_qc"] = {
            "roll_bias": _safe_float(getattr(oq, 'roll_bias', 0)),
            "roll_bias_std": _safe_float(getattr(oq, 'roll_bias_std', 0)),
            "roll_status": getattr(oq, 'roll_status', 'N/A'),
        }

    # PDS computed records
    if hasattr(result, 'computed') and result.computed:
        d["computed_count"] = len(result.computed)

    # Tide records
    if hasattr(result, 'tide_records'):
        d["tide_records"] = int(result.tide_records)

    offset_validation = getattr(result, "offset_validation", None)
    om_provenance = None
    if offset_validation is not None:
        provenance = getattr(offset_validation, "provenance", None) or {}
        if isinstance(provenance, dict):
            om_provenance = provenance.get("om")
        if isinstance(om_provenance, dict):
            d["offset_validation"] = {
                "overall": getattr(offset_validation, "overall", ""),
                "provenance": {"om": om_provenance},
            }
            d["provenance"] = {"om": om_provenance}
            d["provenance_summary"] = _extract_job_provenance_summary(d)
            provenance_manifest = _extract_job_provenance_manifest(d)
            if provenance_manifest:
                d["provenance_manifest"] = provenance_manifest

    return d


# ── Provenance helpers ────────────────────────────────────

def _extract_job_provenance_summary(result: dict | None) -> dict:
    """Return the compact provenance summary for a serialized QC job payload."""
    payload = result if isinstance(result, dict) else {}
    stored_summary = payload.get("provenance_summary")
    if isinstance(stored_summary, dict):
        return stored_summary
    try:
        from desktop.services.data_service import DataService

        return DataService.extract_provenance_summary(payload)
    except Exception:
        return {}


def _extract_job_provenance_manifest(result: dict | None) -> dict:
    """Return the compact provenance manifest for a serialized QC job payload."""
    payload = result if isinstance(result, dict) else {}
    stored_manifest = payload.get("provenance_manifest")
    if isinstance(stored_manifest, dict):
        return stored_manifest
    try:
        from desktop.services.data_service import DataService

        return DataService.extract_provenance_manifest(payload)
    except Exception:
        return {}


def _redact_download_path(value):
    """Collapse a local filesystem path to a stable non-sensitive token."""
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        return _redact_download_payload_paths(value)
    if isinstance(value, list):
        return [_redact_download_path(item) for item in value]
    try:
        return Path(str(value)).name or str(value)
    except Exception:
        return str(value)


def _redact_download_uri(value):
    """Collapse a URI-like value to a safe placeholder."""
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        return _redact_download_payload_paths(value)
    if isinstance(value, list):
        return [_redact_download_uri(item) for item in value]
    text = str(value)
    if "://" in text:
        return "<redacted:uri>"
    return _redact_download_path(text)


def _is_download_path_key(key) -> bool:
    key_text = str(key).strip().lower()
    collapsed = key_text.replace("_", "")
    return (
        key_text in {"path", "filepath", "file_path", "db_path", "source_path", "source_filepath"}
        or collapsed in {"path", "filepath", "dbpath", "sourcepath", "sourcefilepath"}
        or key_text.endswith("_path")
        or key_text.endswith("_filepath")
    )


def _is_download_uri_key(key) -> bool:
    key_text = str(key).strip().lower()
    collapsed = key_text.replace("_", "")
    return (
        key_text in {"uri", "url", "source_uri", "source_url"}
        or collapsed in {"uri", "url", "sourceuri", "sourceurl"}
        or key_text.endswith("_uri")
        or key_text.endswith("_url")
    )


def _redact_download_payload_paths(value):
    """Redact local path/URI fields from a JSON download payload copy."""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if _is_download_path_key(key):
                redacted[key] = _redact_download_path(item)
            elif _is_download_uri_key(key):
                redacted[key] = _redact_download_uri(item)
            else:
                redacted[key] = _redact_download_payload_paths(item)
        return redacted
    if isinstance(value, list):
        return [_redact_download_payload_paths(item) for item in value]
    return value


def _build_job_download_payload(job: dict | None) -> dict:
    """Return a machine-readable QC snapshot for JSON downloads."""
    if not isinstance(job, dict):
        return {}

    payload = job.get("result")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}

    if not payload:
        return {}

    payload = _redact_download_payload_paths(dict(payload))
    provenance_summary = payload.get("provenance_summary") or _extract_job_provenance_summary(payload)
    if provenance_summary:
        payload["provenance_summary"] = provenance_summary
    provenance_manifest = payload.get("provenance_manifest") or _extract_job_provenance_manifest(payload)
    if provenance_manifest:
        payload["provenance_manifest"] = provenance_manifest
    return payload


# ── Helpers ────────────────────────────────────────────────

def _load_om_configs(
    db_path: str | None = None,
    project_id: int | str | None = None,
    allow_request_path: bool = False,
    return_source: bool = False,
) -> list[dict] | tuple[list[dict], OMResolution]:
    """Load OffsetManager vessel configs via API first, then confirmed project SQLite fallback."""
    try:
        from desktop.services.om_client import OMClient
        configs = OMClient.list_configs()
        if configs:
            return (configs, _describe_om_api_source()) if return_source else configs
    except Exception:
        pass

    path = _resolve_om_db_path(db_path, project_id=project_id, allow_request_path=allow_request_path)
    if not path:
        resolution = _describe_om_resolution(
            db_path,
            project_id=project_id,
            allow_request_path=allow_request_path,
        )
        return ([], resolution) if return_source else []

    try:
        import sqlite3
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, vessel_name, project_name, config_date FROM vessel_configs ORDER BY updated_at DESC"
            ).fetchall()
        configs = [dict(r) for r in rows]
        resolution = _describe_om_resolution(
            db_path,
            project_id=project_id,
            allow_request_path=allow_request_path,
        )
        return (configs, resolution) if return_source else configs
    except Exception:
        resolution = _describe_om_resolution(
            db_path,
            project_id=project_id,
            allow_request_path=allow_request_path,
        )
        return ([], resolution) if return_source else []


def _load_om_config_detail(
    config_id: int,
    db_path: str | None = None,
    project_id: int | str | None = None,
    allow_request_path: bool = False,
    return_source: bool = False,
) -> dict | tuple[dict, dict] | None:
    """Load one OffsetManager config payload via API first, then confirmed project SQLite fallback."""
    try:
        from desktop.services.om_client import OMClient
        detail = OMClient.get_config(int(config_id))
        if detail:
            return detail if not return_source else (detail, _describe_om_api_source())
    except Exception:
        pass

    path = _resolve_om_db_path(db_path, project_id=project_id, allow_request_path=allow_request_path)
    if not path:
        return None if not return_source else (
            None,
            _describe_om_resolution(
                db_path,
                project_id=project_id,
                allow_request_path=allow_request_path,
            ),
        )

    try:
        import sqlite3
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            config = conn.execute(
                "SELECT * FROM vessel_configs WHERE id = ?",
                (int(config_id),)
            ).fetchone()
            if not config:
                return None
            sensors = conn.execute(
                "SELECT * FROM sensor_offsets WHERE config_id = ? ORDER BY sensor_type, sensor_name",
                (int(config_id),)
            ).fetchall()
        detail = {
            "config": dict(config),
            "offsets": [dict(s) for s in sensors],
        }
        return detail if not return_source else (
            detail,
            _describe_om_resolution(
                db_path,
                project_id=project_id,
                allow_request_path=allow_request_path,
            ),
        )
    except Exception:
        return None if not return_source else (
            None,
            _describe_om_resolution(
                db_path,
                project_id=project_id,
                allow_request_path=allow_request_path,
            ),
        )


# ── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(port=5103, debug=os.environ.get("FLASK_DEBUG", "0") == "1")

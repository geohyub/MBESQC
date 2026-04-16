"""MBESQC web <-> OffsetManager boundary smoke tests.

These tests verify the legacy Flask app prefers the OffsetManager API contract
and keeps project metadata synchronized without needing the direct DB path in
the default flow.
"""

from __future__ import annotations

import json
import os
import re
import sys
import sqlite3

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SHARED = os.path.join(_ROOT, "..", "..", "_shared")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SHARED)

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(autouse=True)
def _reset_state():
    import web_app
    from desktop.services import om_client

    web_app._jobs.clear()
    web_app._projects.clear()
    if hasattr(om_client.OMClient, "configure"):
        om_client.OMClient.configure(base_url=None, timeout=None)
    yield
    web_app._jobs.clear()
    web_app._projects.clear()
    if hasattr(om_client.OMClient, "configure"):
        om_client.OMClient.configure(base_url=None, timeout=None)


def _create_offsetmanager_db(
    db_path,
    *,
    vessel_name="Geo Vessel",
    project_name="MBESQC Demo",
    config_date="2026-04-03",
    review_state="approved",
    include_sensor_offsets=True,
):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE vessel_configs (
                id INTEGER PRIMARY KEY,
                vessel_name TEXT,
                project_name TEXT,
                config_date TEXT,
                reference_point TEXT,
                description TEXT,
                review_state TEXT,
                review_by TEXT,
                review_at TEXT,
                review_note TEXT,
                updated_at TEXT
            )
            """
        )
        if include_sensor_offsets:
            conn.execute(
                """
                CREATE TABLE sensor_offsets (
                    id INTEGER PRIMARY KEY,
                    config_id INTEGER,
                    sensor_name TEXT,
                    sensor_type TEXT,
                    x_offset REAL,
                    y_offset REAL,
                    z_offset REAL,
                    roll_offset REAL,
                    pitch_offset REAL,
                    heading_offset REAL,
                    latency REAL,
                    notes TEXT
                )
                """
            )
        conn.execute(
            """
            INSERT INTO vessel_configs
            (id, vessel_name, project_name, config_date, reference_point, description, review_state, review_by, review_at, review_note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (7, vessel_name, project_name, config_date, "AP", "Legacy payload", review_state, "Codex", "2026-04-03T12:00:00", "", "2026-04-03T12:00:00"),
        )
        if include_sensor_offsets:
            conn.execute(
                """
                INSERT INTO sensor_offsets
                (id, config_id, sensor_name, sensor_type, x_offset, y_offset, z_offset, roll_offset, pitch_offset, heading_offset, latency, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, 7, "MBES", "MBES Transducer", 1.25, -0.5, 3.0, 0.1, 0.2, 0.3, 0.01, "sample"),
            )
        conn.commit()


def test_load_om_configs_prefers_api(monkeypatch):
    import web_app
    from desktop.services import om_client

    monkeypatch.setattr(
        om_client.OMClient,
        "list_configs",
        staticmethod(lambda: [
            {
                "id": 7,
                "vessel_name": "Geo Vessel",
                "project_name": "MBESQC Demo",
                "config_date": "2026-04-03",
                "readiness_label": "Ready",
            }
        ]),
    )

    configs = web_app._load_om_configs()
    assert configs
    assert configs[0]["id"] == 7
    assert configs[0]["vessel_name"] == "Geo Vessel"


def test_om_client_uses_env_base_url_override(monkeypatch):
    from desktop.services import om_client

    calls = []

    class _Response:
        ok = True

        @staticmethod
        def json():
            return []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return _Response()

    monkeypatch.setenv("MBESQC_OM_BASE_URL", "https://offsetmanager.example:5302/")
    monkeypatch.setattr(om_client.requests, "get", fake_get)

    assert om_client.OMClient.list_configs() == []
    assert calls == [("https://offsetmanager.example:5302/api/configs", 2)]


def test_om_client_falls_back_to_default_for_invalid_env_base_url(monkeypatch):
    from desktop.services import om_client

    calls = []

    class _Response:
        ok = True

        @staticmethod
        def json():
            return []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return _Response()

    monkeypatch.setenv("MBESQC_OM_BASE_URL", "not-a-url")
    monkeypatch.setattr(om_client.requests, "get", fake_get)

    assert om_client.OMClient.list_configs() == []
    assert calls == [("http://localhost:5302/api/configs", 2)]


def test_om_resolution_is_unresolved_without_explicit_path(monkeypatch, tmp_path):
    import web_app

    monkeypatch.delenv("MBESQC_OFFSET_DB_PATH", raising=False)
    monkeypatch.delenv("MBESQC_OM_DB_PATH", raising=False)
    tmp_db = tmp_path / "env_offset_smoke.db"
    with sqlite3.connect(tmp_db):
        pass
    monkeypatch.setenv("MBESQC_OFFSET_DB_PATH", str(tmp_db))
    web_app._projects.clear()

    resolution = web_app._describe_om_resolution()

    assert resolution["source"] == "unresolved"
    assert resolution["path"] == ""
    assert resolution["exists"] is False
    assert resolution["mode"] == "api-first"
    assert resolution["fallback_scope"] == "none"
    assert "API" in resolution["hint"]


def test_api_om_configs_ignores_env_fallback_without_explicit_path(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE vessel_configs (
                id INTEGER PRIMARY KEY,
                vessel_name TEXT,
                project_name TEXT,
                config_date TEXT,
                reference_point TEXT,
                description TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO vessel_configs
            (id, vessel_name, project_name, config_date, reference_point, description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "Geo Vessel", "MBESQC Demo", "2026-04-03", "AP", "Env-only payload", "2026-04-03T12:00:00"),
        )
        conn.commit()

    monkeypatch.setenv("MBESQC_OFFSET_DB_PATH", str(db_path))
    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))
    web_app._projects.clear()

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_api_om_configs_requires_opt_in_for_request_db_path(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE vessel_configs (
                id INTEGER PRIMARY KEY,
                vessel_name TEXT,
                project_name TEXT,
                config_date TEXT,
                reference_point TEXT,
                description TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO vessel_configs
            (id, vessel_name, project_name, config_date, reference_point, description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "Geo Vessel", "MBESQC Demo", "2026-04-03", "AP", "Request-only payload", "2026-04-03T12:00:00"),
        )
        conn.commit()

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))
    web_app._projects.clear()

    client = web_app.app.test_client()
    resp = client.get(
        "/api/om-configs",
        query_string={
            "db_path": str(db_path),
            "include_provenance": "1",
        },
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["configs"] == []
    assert payload["count"] == 0
    assert payload["provenance"]["source"] == "unresolved"
    assert payload["provenance"]["request_path_supplied"] is True
    assert payload["provenance"]["request_fallback_enabled"] is False
    assert payload["provenance"]["project_fallback_configured"] is False
    assert payload["provenance"]["project_fallback_enabled"] is False
    assert "명시적 SQLite fallback 사용" in payload["provenance"]["hint"]


def test_api_om_configs_blocks_unconfirmed_project_fallback(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path)

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": False,
    }

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs?project_id=1&include_provenance=1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["configs"] == []
    assert payload["count"] == 0
    assert payload["provenance"]["source"] == "unresolved"
    assert payload["provenance"]["project_fallback_configured"] is True
    assert payload["provenance"]["project_fallback_enabled"] is False
    assert "fallback 확인이 꺼져 있어" in payload["provenance"]["hint"]


def test_api_om_configs_returns_provenance_envelope_for_project_fallback(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path)

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
    }

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs?project_id=1&include_provenance=1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["count"] == 1
    assert payload["configs"][0]["vessel_name"] == "Geo Vessel"
    assert payload["provenance"]["source"] == "project"
    assert payload["provenance"]["mode"] == "explicit-sqlite-fallback"
    assert payload["provenance"]["fallback_scope"] == "project"
    assert payload["provenance"]["project_fallback_configured"] is True
    assert payload["provenance"]["project_fallback_enabled"] is True
    assert payload["provenance"]["semantic_state"] == "verified"
    assert payload["provenance"]["semantic_hint"]
    assert isinstance(payload["provenance"]["semantic_checks"], list)
    assert any(check["name"] == "review_state_approved" and check["passed"] for check in payload["provenance"]["semantic_checks"])


def test_api_om_configs_blocks_project_fallback_when_review_state_is_not_approved(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path, review_state="pending")

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
    }

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs?project_id=1&include_provenance=1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["configs"] == []
    assert payload["count"] == 0
    assert payload["provenance"]["source"] == "unresolved"
    assert payload["provenance"]["semantic_state"] == "review-risk"
    assert payload["provenance"]["project_fallback_enabled"] is False
    assert "review_state" in payload["provenance"]["semantic_hint"]
    assert any(check["name"] == "review_state_approved" and not check["passed"] for check in payload["provenance"]["semantic_checks"])


def test_api_om_configs_blocks_project_fallback_when_project_metadata_mismatches_db(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path, vessel_name="Old Vessel", project_name="Old Project")

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
    }

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs?project_id=1&include_provenance=1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["configs"] == []
    assert payload["count"] == 0
    assert payload["provenance"]["source"] == "unresolved"
    assert payload["provenance"]["semantic_state"] == "stale-risk"
    assert payload["provenance"]["project_fallback_enabled"] is False
    assert "일치하지 않아" in payload["provenance"]["semantic_hint"]


def test_api_om_configs_blocks_project_fallback_when_schema_is_incomplete(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path, include_sensor_offsets=False)

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
    }

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs?project_id=1&include_provenance=1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["configs"] == []
    assert payload["count"] == 0
    assert payload["provenance"]["source"] == "unresolved"
    assert payload["provenance"]["semantic_state"] == "schema-risk"
    assert "sensor_offsets" in payload["provenance"]["semantic_hint"]


def test_api_om_sensors_normalizes_api_payload(monkeypatch):
    import web_app
    from desktop.services import om_client

    monkeypatch.setattr(
        om_client.OMClient,
        "get_config",
        staticmethod(
            lambda config_id: {
                "config": {
                    "id": config_id,
                    "vessel_name": "Geo Vessel",
                    "project_name": "MBESQC Demo",
                    "config_date": "2026-04-03",
                    "reference_point": "AP",
                    "description": "API payload",
                },
                "offsets": [
                    {
                        "id": 1,
                        "sensor_name": "MBES",
                        "sensor_type": "MBES Transducer",
                        "x_offset": 1.25,
                        "y_offset": -0.5,
                        "z_offset": 3.0,
                        "roll_offset": 0.1,
                        "pitch_offset": 0.2,
                        "heading_offset": 0.3,
                        "latency": 0.01,
                        "notes": "sample",
                    }
                ],
            }
        ),
    )

    client = web_app.app.test_client()
    resp = client.get("/api/om-sensors/7")
    assert resp.status_code == 200

    data = resp.get_json()
    assert data["config"]["vessel_name"] == "Geo Vessel"
    assert data["total"] == 1
    assert data["source"] == "api"
    assert data["mode"] == "api-first"
    assert data["source_label"] == "OffsetManager API"
    assert data["resolution"]["source"] == "api"
    assert data["resolution"]["fallback_scope"] == "none"
    assert data["provenance"]["om"]["decision"]["source"] == "api"
    assert data["provenance"]["om"]["fallback_candidate"]["source"] == "api"
    assert data["provenance"]["om"]["semantic"]["checks"][0]["name"] == "api_available"
    assert data["sensors"][0]["name"] == "MBES"
    assert data["sensors"][0]["type_ko"] == "멀티빔 (MBES)"
    assert data["sensors"][0]["x"] == pytest.approx(1.25)


def test_edit_project_syncs_om_config_id():
    import web_app

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
    }

    client = web_app.app.test_client()
    resp = client.post(
        "/project/1/edit",
        data={
            "project_name": "Project 1",
            "offset_config_id": "12",
        },
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)
    assert web_app._projects[1]["om_config_id"] == 12
    assert web_app._projects[1]["offset_config_id"] == 12


def test_edit_project_blocks_stale_project_fallback_confirmation(monkeypatch, tmp_path):
    import web_app

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path, vessel_name="Old Vessel", project_name="Old Project")

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
    }

    client = web_app.app.test_client()
    resp = client.post(
        "/project/1/edit",
        data={
            "project_name": "Project 1",
            "vessel_name": "Geo Vessel",
            "offset_db_path": str(db_path),
            "offset_db_path_confirmed": "1",
        },
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)
    assert web_app._projects[1]["offset_db_path_confirmed"] is False


def test_project_specific_db_path_is_used_when_api_is_unavailable(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path)

    web_app._projects[1] = {
        "id": 1,
        "name": "MBESQC Demo",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
        "om_config_id": 7,
        "offset_config_id": 7,
    }

    monkeypatch.setattr(om_client.OMClient, "list_configs", staticmethod(lambda: []))
    monkeypatch.setattr(om_client.OMClient, "get_config", staticmethod(lambda config_id: None))

    client = web_app.app.test_client()
    resp = client.get("/api/om-configs?project_id=1")
    assert resp.status_code == 200
    configs = resp.get_json()
    assert len(configs) == 1
    assert configs[0]["vessel_name"] == "Geo Vessel"

    resp = client.get("/api/om-sensors/7?project_id=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["config"]["vessel_name"] == "Geo Vessel"
    assert data["total"] == 1
    assert data["source"] == "project"
    assert data["mode"] == "explicit-sqlite-fallback"
    assert data["path"] == str(db_path)
    assert data["exists"] is True
    assert data["resolution"]["source"] == "project"
    assert data["resolution"]["fallback_scope"] == "project"
    assert data["provenance"]["om"]["decision"]["source"] == "project"
    assert data["provenance"]["om"]["fallback_candidate"]["source"] == "project"
    assert data["sensors"][0]["name"] == "MBES"


def test_verify_offsets_uses_project_specific_db_path_when_api_is_unavailable(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    db_path = tmp_path / "offsets.db"
    _create_offsetmanager_db(db_path)

    web_app._projects[1] = {
        "id": 1,
        "name": "MBESQC Demo",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
        "offset_db_path_confirmed": True,
        "om_config_id": 7,
        "offset_config_id": 7,
    }

    monkeypatch.setattr(om_client.OMClient, "get_config", staticmethod(lambda config_id: None))
    monkeypatch.setattr(web_app.os.path, "isfile", lambda _: True)

    class _Meta:
        sections = {"GEOMETRY": {"Offset(MBES)": "MBES,1,2,3"}}

    monkeypatch.setattr(web_app, "read_pds_header", None, raising=False)
    import pds_toolkit
    monkeypatch.setattr(pds_toolkit, "read_pds_header", lambda _: _Meta())

    client = web_app.app.test_client()
    resp = client.post(
        "/api/verify-offsets",
        json={
            "pds_path": "dummy.pds",
            "om_config_id": 7,
            "project_id": 1,
            "offset_db_path": str(db_path),
            "allow_sqlite_fallback": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["om_lookup"]["source"] == "request"
    assert data["om_lookup"]["path"] == str(db_path)
    assert data["om_lookup"]["request_path_supplied"] is True
    assert data["om_lookup"]["request_fallback_enabled"] is True
    assert data["om_comparison"]["source"] == "request"
    assert data["om_comparison"]["fallback_source"] == "request"
    assert data["om_comparison"]["fallback_mode"] == "explicit-sqlite-fallback"
    assert data["om_comparison"]["resolution"]["fallback_scope"] == "request"
    assert data["om_comparison"]["fallback_resolution"]["source"] == "request"
    assert data["om_comparison"]["semantic_state"] == "verified"
    assert isinstance(data["om_comparison"]["semantic_checks"], list)
    assert data["provenance"]["om"]["decision"]["source"] == "request"
    assert data["provenance"]["om"]["fallback_candidate"]["source"] == "request"
    assert data["provenance"]["om"]["semantic"]["state"] == "verified"
    assert data["om_comparison"]["vessel"] == "Geo Vessel"


def test_edit_project_clears_project_fallback_confirmation_when_path_changes():
    import web_app

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": r"E:\Software\Preprocessing\OffsetManager\offsets.db",
        "offset_db_path_confirmed": True,
    }

    client = web_app.app.test_client()
    resp = client.post(
        "/project/1/edit",
        data={
            "project_name": "Project 1",
            "offset_db_path": r"E:\Temp\offsets-new.db",
        },
        follow_redirects=False,
    )

    assert resp.status_code in (302, 303)
    assert web_app._projects[1]["offset_db_path"].endswith("offsets-new.db")
    assert web_app._projects[1]["offset_db_path_confirmed"] is False


def test_verify_offsets_reports_api_source_without_db_path(monkeypatch):
    import web_app
    from desktop.services import om_client

    monkeypatch.delenv("MBESQC_OFFSET_DB_PATH", raising=False)
    monkeypatch.delenv("MBESQC_OM_DB_PATH", raising=False)
    monkeypatch.setattr(
        om_client.OMClient,
        "get_config",
        staticmethod(
            lambda config_id: {
                "config": {
                    "id": config_id,
                    "vessel_name": "Geo Vessel",
                    "project_name": "MBESQC Demo",
                    "config_date": "2026-04-03",
                    "reference_point": "AP",
                    "description": "API payload",
                },
                "offsets": [
                    {
                        "id": 1,
                        "sensor_name": "MBES",
                        "sensor_type": "MBES Transducer",
                        "x_offset": 1.25,
                        "y_offset": -0.5,
                        "z_offset": 3.0,
                        "roll_offset": 0.1,
                        "pitch_offset": 0.2,
                        "heading_offset": 0.3,
                        "latency": 0.01,
                        "notes": "sample",
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(web_app.os.path, "isfile", lambda _: True)

    class _Meta:
        sections = {"GEOMETRY": {"Offset(MBES)": "MBES,1,2,3"}}

    import pds_toolkit
    monkeypatch.setattr(pds_toolkit, "read_pds_header", lambda _: _Meta())

    client = web_app.app.test_client()
    resp = client.post(
        "/api/verify-offsets",
        json={
            "pds_path": "dummy.pds",
            "om_config_id": 7,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["om_lookup"]["source"] == "unresolved"
    assert data["om_comparison"]["source"] == "api"
    assert data["om_comparison"]["mode"] == "api-first"
    assert data["om_comparison"]["resolution"]["source"] == "api"
    assert data["om_lookup"]["fallback_scope"] == "none"
    assert data["om_comparison"]["fallback_source"] == "unresolved"
    assert data["om_comparison"]["fallback_resolution"]["source"] == "unresolved"
    assert data["om_comparison"]["provenance"]["decision"]["source"] == "api"
    assert data["om_comparison"]["provenance"]["fallback_candidate"]["source"] == "unresolved"
    assert data["provenance"]["om"]["decision"]["source"] == "api"
    assert data["provenance"]["om"]["fallback_candidate"]["source"] == "unresolved"


def test_verify_offsets_request_sqlite_uses_request_db_semantics(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    project_db = tmp_path / "project_offsets.db"
    request_db = tmp_path / "request_offsets.db"
    _create_offsetmanager_db(project_db, vessel_name="Geo Vessel", project_name="Project Alpha", review_state="approved")
    _create_offsetmanager_db(request_db, vessel_name="Other Vessel", project_name="Other Project", review_state="pending")

    web_app._projects[1] = {
        "id": 1,
        "name": "Project Alpha",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(project_db),
        "offset_db_path_confirmed": True,
        "om_config_id": 7,
        "offset_config_id": 7,
    }

    monkeypatch.setattr(om_client.OMClient, "get_config", staticmethod(lambda config_id: None))
    monkeypatch.setattr(web_app.os.path, "isfile", lambda _: True)

    class _Meta:
        sections = {"GEOMETRY": {"Offset(MBES)": "MBES,1,2,3"}}

    import pds_toolkit
    monkeypatch.setattr(pds_toolkit, "read_pds_header", lambda _: _Meta())

    client = web_app.app.test_client()
    resp = client.post(
        "/api/verify-offsets",
        json={
            "pds_path": "dummy.pds",
            "om_config_id": 7,
            "project_id": 1,
            "offset_db_path": str(request_db),
            "allow_sqlite_fallback": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["om_lookup"]["source"] == "request"
    assert data["om_lookup"]["path"] == str(request_db)
    assert data["om_lookup"]["semantic_state"] == "review-risk"
    assert data["om_lookup"]["latest_review_state"] == "pending"
    assert any(
        check["name"] == "review_state_approved" and not check["passed"]
        for check in data["om_lookup"]["semantic_checks"]
    )


def test_verify_offsets_selected_api_provenance_uses_api_semantics(monkeypatch, tmp_path):
    import web_app
    from desktop.services import om_client

    project_db = tmp_path / "project_offsets.db"
    request_db = tmp_path / "request_offsets.db"
    _create_offsetmanager_db(project_db, vessel_name="Geo Vessel", project_name="Project Alpha", review_state="approved")
    _create_offsetmanager_db(request_db, vessel_name="Other Vessel", project_name="Other Project", review_state="pending")

    web_app._projects[1] = {
        "id": 1,
        "name": "Project Alpha",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(project_db),
        "offset_db_path_confirmed": True,
        "om_config_id": 7,
        "offset_config_id": 7,
    }

    monkeypatch.setattr(
        om_client.OMClient,
        "get_config",
        staticmethod(
            lambda config_id: {
                "config": {
                    "id": config_id,
                    "vessel_name": "Geo Vessel",
                    "project_name": "Project Alpha",
                    "config_date": "2026-04-03",
                    "reference_point": "AP",
                    "description": "API payload",
                },
                "offsets": [
                    {
                        "id": 1,
                        "sensor_name": "MBES",
                        "sensor_type": "MBES Transducer",
                        "x_offset": 1.25,
                        "y_offset": -0.5,
                        "z_offset": 3.0,
                        "roll_offset": 0.1,
                        "pitch_offset": 0.2,
                        "heading_offset": 0.3,
                        "latency": 0.01,
                        "notes": "sample",
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(web_app.os.path, "isfile", lambda _: True)

    class _Meta:
        sections = {"GEOMETRY": {"Offset(MBES)": "MBES,1,2,3"}}

    import pds_toolkit
    monkeypatch.setattr(pds_toolkit, "read_pds_header", lambda _: _Meta())

    client = web_app.app.test_client()
    resp = client.post(
        "/api/verify-offsets",
        json={
            "pds_path": "dummy.pds",
            "om_config_id": 7,
            "project_id": 1,
            "offset_db_path": str(request_db),
            "allow_sqlite_fallback": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["om_comparison"]["source"] == "api"
    assert data["om_comparison"]["fallback_source"] == "request"
    assert data["om_comparison"]["semantic_state"] == "verified"
    assert data["provenance"]["om"]["decision"]["source"] == "api"
    assert data["provenance"]["om"]["semantic"]["state"] == "verified"
    assert data["provenance"]["om"]["semantic"]["checks"][0]["name"] == "api_available"


def test_api_status_exposes_persisted_provenance_summary(monkeypatch):
    import web_app
    from desktop.services.data_service import DataService

    stored_summary = {
        "has_data": True,
        "source": "db",
        "mode": "sqlite",
        "path": r"E:\\Temp\\offsets.db",
        "semantic_state": "verified",
        "semantic_hint": "persisted summary",
        "semantic_checks_total": 2,
        "semantic_checks_passed": 2,
        "request_fallback_enabled": False,
        "project_fallback_enabled": True,
        "project_fallback_configured": True,
        "fallback_scope": "project",
    }
    stored_manifest = {
        "type": "mbesqc.provenance-manifest",
        "version": 1,
        "summary": stored_summary,
        "has_data": True,
        "source": "db",
        "mode": "sqlite",
        "path": r"E:\\Temp\\offsets.db",
        "semantic_state": "verified",
        "semantic_hint": "persisted summary",
        "semantic_checks_total": 2,
        "semantic_checks_passed": 2,
        "request_fallback_enabled": False,
        "project_fallback_enabled": True,
        "project_fallback_configured": True,
        "fallback_scope": "project",
    }

    web_app._jobs[1] = {
        "id": 1,
        "status": "done",
        "progress": "Complete",
        "result": {
            "provenance_summary": stored_summary,
            "provenance_manifest": stored_manifest,
            "offset_validation": {
                "provenance": {
                    "om": {
                        "decision": {
                            "source": "api",
                            "mode": "api-first",
                            "path": "",
                        },
                        "semantic": {
                            "state": "unresolved",
                            "hint": "raw payload should be ignored when summary exists",
                            "checks": [],
                        },
                    }
                }
            },
        },
    }
    monkeypatch.setattr(
        DataService,
        "extract_provenance_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected raw provenance summary derivation")),
    )
    monkeypatch.setattr(
        DataService,
        "extract_provenance_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected raw provenance manifest derivation")),
    )

    client = web_app.app.test_client()
    resp = client.get("/api/status/1")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "done"
    assert payload["provenance_summary"] == stored_summary
    assert payload["provenance_manifest"] == stored_manifest


def test_api_download_json_exposes_persisted_provenance_snapshot():
    import web_app

    stored_summary = {
        "has_data": True,
        "source": "db",
        "mode": "sqlite",
        "path": r"E:\\Temp\\offsets.db",
        "semantic_state": "verified",
        "semantic_hint": "persisted summary",
        "semantic_checks_total": 2,
        "semantic_checks_passed": 2,
        "request_fallback_enabled": False,
        "project_fallback_enabled": True,
        "project_fallback_configured": True,
        "fallback_scope": "project",
    }

    web_app._jobs[1] = {
        "id": 1,
        "status": "done",
        "progress": "Complete",
        "result": {
            "provenance_summary": stored_summary,
            "offset_validation": {
                "provenance": {
                    "om": {
                        "decision": {
                            "source": "api",
                            "mode": "api-first",
                            "path": "",
                        },
                        "semantic": {
                            "state": "unresolved",
                            "hint": "raw payload should be ignored when summary exists",
                            "checks": [],
                        },
                    }
                }
            }
        },
    }

    client = web_app.app.test_client()
    resp = client.get("/api/download/1/json")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "QC_Result_1.json" in resp.headers["Content-Disposition"]

    payload = json.loads(resp.data.decode("utf-8"))
    assert payload["provenance_summary"] == stored_summary


def test_qc_result_page_embeds_persisted_provenance_summary_json():
    import web_app

    stored_summary = {
        "has_data": True,
        "source": "db",
        "mode": "sqlite",
        "path": r"E:\\Temp\\offsets.db",
        "semantic_state": "verified",
        "semantic_hint": "persisted summary",
        "semantic_checks_total": 2,
        "semantic_checks_passed": 2,
        "request_fallback_enabled": False,
        "project_fallback_enabled": True,
        "project_fallback_configured": True,
        "fallback_scope": "project",
    }

    web_app._jobs[1] = {
        "id": 1,
        "status": "done",
        "progress": "Complete",
        "pds_path": "dummy.pds",
        "result": {
            "provenance_summary": stored_summary,
            "vessel_name": "Geo Vessel",
            "total_pings": 123,
            "total_beams": 456,
            "depth_range": [1.0, 2.0],
            "nav_records": 10,
            "lat_range": [1.1, 1.2],
            "lon_range": [2.1, 2.2],
            "attitude_records": 5,
            "elapsed_sec": 12.3,
        },
    }

    client = web_app.app.test_client()
    resp = client.get("/qc/1")

    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="provenance-summary-1"' in html

    match = re.search(
        r'<script type="application/json" id="provenance-summary-1">\s*(\{.*?\})\s*</script>',
        html,
        re.S,
    )
    assert match is not None
    assert json.loads(match.group(1)) == stored_summary

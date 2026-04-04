"""MBESQC web <-> OffsetManager boundary smoke tests.

These tests verify the legacy Flask app prefers the OffsetManager API contract
and keeps project metadata synchronized without needing the direct DB path in
the default flow.
"""

from __future__ import annotations

import os
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

    web_app._jobs.clear()
    web_app._projects.clear()
    yield
    web_app._jobs.clear()
    web_app._projects.clear()


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


def test_project_specific_db_path_is_used_when_api_is_unavailable(monkeypatch, tmp_path):
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
            (id, vessel_name, project_name, config_date, reference_point, description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "Geo Vessel", "MBESQC Demo", "2026-04-03", "AP", "Legacy payload", "2026-04-03T12:00:00"),
        )
        conn.execute(
            """
            INSERT INTO sensor_offsets
            (id, config_id, sensor_name, sensor_type, x_offset, y_offset, z_offset, roll_offset, pitch_offset, heading_offset, latency, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 7, "MBES", "MBES Transducer", 1.25, -0.5, 3.0, 0.1, 0.2, 0.3, 0.01, "sample"),
        )
        conn.commit()

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
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
    assert data["mode"] == "db-fallback"
    assert data["path"] == str(db_path)
    assert data["exists"] is True
    assert data["sensors"][0]["name"] == "MBES"


def test_verify_offsets_uses_project_specific_db_path_when_api_is_unavailable(monkeypatch, tmp_path):
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
            (id, vessel_name, project_name, config_date, reference_point, description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "Geo Vessel", "MBESQC Demo", "2026-04-03", "AP", "Legacy payload", "2026-04-03T12:00:00"),
        )
        conn.commit()

    web_app._projects[1] = {
        "id": 1,
        "name": "Project 1",
        "vessel": "Geo Vessel",
        "created_at": "2026-04-03T00:00:00",
        "pds_files": [],
        "jobs": [],
        "offset_db_path": str(db_path),
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
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["om_lookup"]["source"] == "request"
    assert data["om_lookup"]["path"] == str(db_path)
    assert data["om_comparison"]["source"] == "request"
    assert data["om_comparison"]["fallback_source"] == "request"
    assert data["om_comparison"]["fallback_mode"] == "db-fallback"
    assert data["om_comparison"]["vessel"] == "Geo Vessel"


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

from __future__ import annotations

import json
import importlib
import os
import sys
from datetime import datetime, timezone
from types import ModuleType


_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SHARED = os.path.join(_ROOT, "..", "..", "_shared")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SHARED)


def test_desktop_entrypoint_import_does_not_launch_app(monkeypatch):
    fake_main_module = ModuleType("desktop.main")
    launch_calls = {"count": 0}

    def fake_main():
        launch_calls["count"] += 1
        raise AssertionError("desktop.__main__ launched the app during import")

    fake_main_module.main = fake_main
    monkeypatch.setitem(sys.modules, "desktop.main", fake_main_module)
    sys.modules.pop("desktop.__main__", None)

    module = importlib.import_module("desktop.__main__")

    assert module.launch_app is fake_main
    assert launch_calls["count"] == 0


def test_desktop_main_applies_om_runtime_config_before_run(monkeypatch):
    import desktop.main as desktop_main

    captured = {}

    def fake_configure(*, base_url=None, timeout=None):
        captured["base_url"] = base_url
        captured["timeout"] = timeout

    monkeypatch.setattr(desktop_main.OMClient, "configure", fake_configure)
    monkeypatch.setattr(desktop_main.MBESQCApp, "run", lambda *args, **kwargs: None)

    desktop_main.main(
        om_base_url="https://offsetmanager.example:5302",
        om_timeout_seconds=5,
    )

    assert captured == {
        "base_url": "https://offsetmanager.example:5302",
        "timeout": 5,
    }


def test_om_runtime_resolution_prefers_cli_over_env(monkeypatch):
    from desktop.services.om_client import resolve_runtime_config

    monkeypatch.setenv("MBESQC_OM_BASE_URL", "https://env.example:5302/")
    monkeypatch.setenv("MBESQC_OM_TIMEOUT_SECONDS", "7")

    resolved = resolve_runtime_config(
        om_base_url="https://cli.example:5302/",
        om_timeout_seconds="3",
    )

    assert resolved.base_url == "https://cli.example:5302"
    assert resolved.timeout_seconds == 3.0
    assert resolved.base_url_source == "cli"
    assert resolved.timeout_source == "cli"


def test_desktop_self_check_reports_without_launch(monkeypatch, capsys):
    import desktop.__main__ as desktop_launcher

    launch_calls = {"count": 0}

    def fake_launch_app(*args, **kwargs):
        launch_calls["count"] += 1

    monkeypatch.setattr(desktop_launcher, "launch_app", fake_launch_app)
    monkeypatch.setattr(
        desktop_launcher,
        "build_runtime_report",
        lambda **kwargs: {
            "boundary": "api-first",
            "fallback_policy": "explicit-only",
            "base_url": "https://offsetmanager.example:5302",
            "base_url_source": "cli",
            "timeout_seconds": 4.0,
            "timeout_source": "cli",
            "api_reachable": True,
        },
    )

    exit_code = desktop_launcher.main(
        [
            "--self-check",
            "--om-base-url",
            "https://offsetmanager.example:5302",
            "--om-timeout-seconds",
            "4",
        ]
    )

    captured = capsys.readouterr().out

    assert exit_code == 0
    assert launch_calls["count"] == 0
    assert "MBESQC OffsetManager self-check" in captured
    assert "https://offsetmanager.example:5302" in captured


def test_desktop_self_check_can_write_bounded_report(monkeypatch, capsys, tmp_path):
    import desktop.__main__ as desktop_launcher

    launch_calls = {"count": 0}
    fixed_report = {
        "boundary": "api-first",
        "fallback_policy": "explicit-only",
        "base_url": "https://offsetmanager.example:5302",
        "base_url_source": "cli",
        "timeout_seconds": 4.0,
        "timeout_source": "cli",
        "api_reachable": True,
    }

    class _FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 4, 14, 3, 4, 5, tzinfo=timezone.utc)

    def fake_launch_app(*args, **kwargs):
        launch_calls["count"] += 1

    monkeypatch.setattr(desktop_launcher, "launch_app", fake_launch_app)
    monkeypatch.setattr(desktop_launcher, "build_runtime_report", lambda **kwargs: fixed_report)
    monkeypatch.setattr(desktop_launcher, "datetime", _FixedDateTime)

    report_path = tmp_path / "self-check-report.json"

    exit_code = desktop_launcher.main(
        [
            "--self-check",
            "--self-check-report",
            str(report_path),
            "--om-base-url",
            "https://offsetmanager.example:5302",
            "--om-timeout-seconds",
            "4",
        ]
    )

    captured = capsys.readouterr().out
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert launch_calls["count"] == 0
    assert captured == desktop_launcher.format_runtime_report(fixed_report) + "\n"
    assert payload == {
        "type": "mbesqc.self-check-report",
        "version": 1,
        "generated_at": "2026-04-14T03:04:05Z",
        "boundary": "api-first",
        "fallback_policy": "explicit-only",
        "base_url": "https://offsetmanager.example:5302",
        "base_url_source": "cli",
        "timeout_seconds": 4.0,
        "timeout_source": "cli",
        "api_reachable": True,
        "probe": "/api/configs",
        "note": "API-first runtime settings only; no OffsetManager data integrity or project DB proof.",
    }


def test_desktop_self_check_report_does_not_require_stdout(monkeypatch, tmp_path):
    import desktop.__main__ as desktop_launcher

    fixed_report = {
        "boundary": "api-first",
        "fallback_policy": "explicit-only",
        "base_url": "https://offsetmanager.example:5302",
        "base_url_source": "cli",
        "timeout_seconds": 4.0,
        "timeout_source": "cli",
        "api_reachable": True,
    }

    class _FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 4, 14, 3, 4, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(desktop_launcher, "build_runtime_report", lambda **kwargs: fixed_report)
    monkeypatch.setattr(desktop_launcher, "datetime", _FixedDateTime)
    monkeypatch.setattr(desktop_launcher.sys, "stdout", None)

    report_path = tmp_path / "self-check-report.json"

    exit_code = desktop_launcher.main(
        [
            "--self-check",
            "--self-check-report",
            str(report_path),
        ]
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["type"] == "mbesqc.self-check-report"
    assert payload["generated_at"] == "2026-04-14T03:04:05Z"

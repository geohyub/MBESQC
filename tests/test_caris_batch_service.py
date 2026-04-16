from __future__ import annotations

import logging
import subprocess
import sys
from types import SimpleNamespace


_ROOT = r"E:\Software\QC\MBESQC"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_run_cmd_redacts_sensitive_paths_from_logs(monkeypatch, caplog):
    from desktop.services.caris_batch_service import CarisBatchConfig, CarisBatchRunner

    runner = CarisBatchRunner(CarisBatchConfig())
    runner._exe = r"C:\Program Files\CARIS\HIPS and SIPS\12.1\bin\carisbatch.exe"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        ok, output = runner._run_cmd(
            [
                "ImportToHIPS",
                "--vessel-file",
                r"E:\Sensitive\MBESQC\secrets\vessel.vessel",
                r"E:\Sensitive\MBESQC\raw\line01.gsf",
                "file:///E:/Sensitive/MBESQC/project/project.hips?Vessel=GeoVessel;Day=2025-090",
            ]
        )

    assert ok is True
    assert output == ""
    assert captured["cmd"][1] == "--run"
    assert r"E:\Sensitive\MBESQC\secrets\vessel.vessel" not in caplog.text
    assert r"E:\Sensitive\MBESQC\raw\line01.gsf" not in caplog.text
    assert "file:///E:/Sensitive/MBESQC/project/project.hips" not in caplog.text

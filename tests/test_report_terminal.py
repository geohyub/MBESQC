from __future__ import annotations

import sys
from types import SimpleNamespace


def test_print_terminal_report_does_not_require_stdout(monkeypatch):
    from mbes_qc import report

    writes: list[str] = []

    class _FallbackStream:
        encoding = "utf-8"

        def write(self, text: str) -> None:
            writes.append(text)

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "__stdout__", _FallbackStream())

    report.print_terminal_report(
        {
            "Demo Section": {
                "status": "PASS",
                "items": [],
                "detail": "bounded proof",
            }
        }
    )

    assert writes
    assert any("Demo Section" in chunk for chunk in writes)


def test_print_terminal_report_is_fail_closed_for_empty_payload(monkeypatch):
    from mbes_qc import report

    writes: list[str] = []

    class _FallbackStream:
        encoding = "utf-8"

        def write(self, text: str) -> None:
            writes.append(text)

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "__stdout__", _FallbackStream())

    report.print_terminal_report({})

    output = "".join(writes)
    assert "OVERALL: N/A" in output
    assert "OVERALL: PASS" not in output


def test_print_terminal_report_treats_only_na_items_as_unknown(monkeypatch):
    from mbes_qc import report

    writes: list[str] = []

    class _FallbackStream:
        encoding = "utf-8"

        def write(self, text: str) -> None:
            writes.append(text)

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "__stdout__", _FallbackStream())

    report.print_terminal_report(
        {
            "Demo Section": SimpleNamespace(
                items=[
                    {
                        "name": "Missing proof",
                        "status": "N/A",
                        "detail": "no explicit verdict available",
                    }
                ]
            )
        }
    )

    output = "".join(writes)
    assert "Demo Section" in output
    assert "[N/A" in output
    assert "OVERALL: N/A" in output
    assert "OVERALL: PASS" not in output

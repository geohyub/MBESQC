from __future__ import annotations

import os
import sys


_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SHARED = os.path.join(_ROOT, "..", "..", "_shared")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SHARED)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_resolve_file_result_display_prefers_file_result():
    from desktop.panels.project_detail_panel import _resolve_file_result_display

    display = _resolve_file_result_display(
        7,
        {
            7: {
                "status": "done",
                "score": 88.2,
            }
        },
        {
            "status": "done",
            "score": 91.4,
        },
    )

    assert display["score"] == 88.2
    assert display["status_kind"] == "done"
    assert "DONE" in display["status_text"]


def test_resolve_file_result_display_uses_project_only_marker_when_only_snapshot_exists():
    from desktop.panels.project_detail_panel import _resolve_file_result_display

    display = _resolve_file_result_display(
        9,
        {},
        {
            "status": "done",
            "score": 92.0,
        },
    )

    assert display["score"] is None
    assert display["status_kind"] == "project_only"
    assert display["status_text"] == "SEE PROJECT QC"


def test_build_dashboard_route_marks_sampled_preview_without_fake_pass_status():
    from desktop.panels.project_detail_panel import _build_dashboard_route

    route = _build_dashboard_route(
        file_id=3,
        file_name="Line03.gsf",
        lats=[37.1, 37.2],
        lons=[126.1, 126.2],
        sampled_preview=True,
    )

    assert route.name.endswith("(Sampled preview)")
    assert route.status == "PREVIEW"
    assert route.grade == "--"


def test_build_dashboard_route_preserves_real_status_for_analyzed_lines():
    from desktop.panels.project_detail_panel import _build_dashboard_route

    route = _build_dashboard_route(
        file_id=5,
        file_name="Line05.gsf",
        lats=[37.1, 37.2],
        lons=[126.1, 126.2],
        score=81.5,
        grade="A",
        status="PASS",
        sampled_preview=True,
    )

    assert route.name.endswith("(Sampled preview)")
    assert route.status == "PASS"
    assert route.grade == "A"

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.join(os.path.dirname(__file__), "..")
SHARED = os.path.join(ROOT, "..", "..", "_shared")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)
    yield _app


def test_analysis_panel_track_selection_syncs_combo_and_table(app):
    from desktop.panels.analysis_panel import AnalysisPanel

    panel = AnalysisPanel()
    try:
        lines = [
            {"name": "Line A", "lats": [0.0, 1.0], "lons": [10.0, 11.0]},
            {"name": "Line B", "lats": [2.0, 3.0], "lons": [12.0, 13.0]},
        ]

        panel._populate_line_table(lines)
        panel._populate_track_plot({"lines": lines})
        panel._on_track_line_clicked("Line B")

        assert panel._line_filter.currentText() == "Line B"
        assert panel._line_table.currentRow() == 1
    finally:
        panel.close()


def test_analysis_panel_track_plot_uses_serialized_track_lines(app):
    from desktop.panels.analysis_panel import AnalysisPanel

    panel = AnalysisPanel()
    try:
        section = {
            "lines": [
                {"name": "Line A"},
                {"name": "Line B"},
            ],
            "track_lines": [
                {"name": "Line A", "lats": [0.0, 1.0], "lons": [10.0, 11.0]},
                {"name": "Line B", "lats": [2.0, 3.0], "lons": [12.0, 13.0]},
            ],
        }

        panel._populate_track_plot(section)

        assert len(panel._track_plot.routes) == 2
        assert [route.name for route in panel._track_plot.routes] == ["Line A", "Line B"]
    finally:
        panel.close()


def test_analysis_panel_places_track_plot_above_line_filter_and_chart(app):
    from desktop.panels.analysis_panel import AnalysisPanel

    panel = AnalysisPanel()
    try:
        layout = panel._track_plot.parentWidget().layout()

        assert layout.indexOf(panel._track_plot) < layout.indexOf(panel._line_filter_frame)
        assert layout.indexOf(panel._track_plot) < layout.indexOf(panel._chart)
    finally:
        panel.close()


def test_analysis_panel_shows_track_plot_when_serialized_track_lines_exist(app):
    from desktop.panels.analysis_panel import AnalysisPanel

    panel = AnalysisPanel()
    try:
        panel._result_data = {
            "coverage": {
                "lines": [],
                "track_lines": [
                    {"name": "Line A", "lats": [0.0, 1.0], "lons": [10.0, 11.0]},
                ],
            }
        }

        panel._on_card_clicked("coverage")

        assert not panel._track_plot.isHidden()
        assert len(panel._track_plot.routes) == 1
    finally:
        panel.close()


def test_mbesqc_dqr_title_stays_uppercase(app):
    from desktop.main import MBESQCApp

    window = MBESQCApp()
    try:
        window._switch_to("dqr")

        assert window.top_bar.title_label.text() == "DQR"
        assert window._breadcrumb_bar._items[-1][1] == "DQR"
    finally:
        window.close()


def test_project_detail_dashboard_track_selection_and_activation(app):
    from desktop.panels.project_detail_panel import ProjectDetailPanel
    from geoview_pyside6.widgets.track_plot import LineRoute

    panel = ProjectDetailPanel()
    emitted: list[tuple[int, int]] = []
    panel.file_selected.connect(lambda file_id, project_id: emitted.append((file_id, project_id)))
    panel._project_id = 3

    route = LineRoute(line_id=77, name="MBES 77", lats=[1.0, 2.0], lons=[30.0, 31.0])
    panel._populate_dash_line_list([route])
    panel._on_dash_track_line_selected(77)
    panel._on_dash_track_line_activated(77)

    try:
        assert panel._dash_line_list.currentRow() == 0
        assert emitted == [(77, 3)]
    finally:
        panel.close()

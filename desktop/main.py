"""MBESQC Desktop -- PySide6 Native Application

Multibeam Echosounder Data Quality Control.
Run: python -m desktop  (from E:/Software/QC/MBESQC/)
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

# ── Path setup ──
_MBESQC_ROOT = str(Path(__file__).resolve().parents[1])
_SHARED_ROOT = str(Path(__file__).resolve().parents[3] / "_shared")

if _MBESQC_ROOT not in sys.path:
    sys.path.insert(0, _MBESQC_ROOT)
if _SHARED_ROOT not in sys.path:
    sys.path.insert(0, _SHARED_ROOT)

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFileDialog, QMenu
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QShortcut, QKeySequence

from geoview_pyside6 import GeoViewApp, Category
from geoview_pyside6.constants import Dark, Font

from desktop.app_controller import AppController
from desktop.widgets.toast import ToastManager
from desktop.services.data_service import DataService
from desktop.panels.dashboard_panel import DashboardPanel
from desktop.panels.project_detail_panel import ProjectDetailPanel
from desktop.panels.upload_panel import UploadPanel
from desktop.panels.analysis_panel import AnalysisPanel
from desktop.panels.project_form_panel import ProjectFormPanel
from desktop.services.export_service import ExportWorker


class MBESQCApp(GeoViewApp):
    """MBESQC Desktop Application -- Multibeam Echosounder QC"""

    APP_NAME = "MBESQC"
    APP_VERSION = "v1.0"
    CATEGORY = Category.QC

    def __init__(self):
        # Initialize DB before building UI
        DataService.init_db()

        # Controller
        self.controller = AppController()

        super().__init__()

        # Toast manager -- content_stack 위에 표시
        self.toast_mgr = ToastManager(self.content_stack)
        self.controller.toast_requested.connect(self.toast_mgr.show_toast)

        # Connect navigation signals
        self._connect_navigation()

        # Keyboard shortcuts
        self._setup_shortcuts()

        # Export state (GC prevention)
        self._export_thread = None
        self._export_worker = None
        self._old_workers = []

    def setup_panels(self):
        """Register all 5 panels."""
        # Dashboard
        self._dashboard = DashboardPanel()
        self._dashboard.project_selected.connect(
            self.controller.navigate_project_detail.emit)
        self._dashboard.new_project.connect(
            self.controller.navigate_form_new.emit)
        self.add_panel("dashboard", "\u25A0", "\ub300\uc2dc\ubcf4\ub4dc", self._dashboard)

        # Project Detail
        self._project_detail = ProjectDetailPanel()
        self._project_detail.file_selected.connect(
            self.controller.navigate_analysis.emit)
        self._project_detail.upload_requested.connect(
            self.controller.navigate_upload.emit)
        self._project_detail.edit_requested.connect(
            self.controller.navigate_form_edit.emit)
        self._project_detail.toast_requested.connect(
            self.controller.toast_requested.emit)
        self.add_panel("project_detail", "\u25C6", "\ud504\ub85c\uc81d\ud2b8", self._project_detail)

        # Upload
        self._upload = UploadPanel()
        self._upload.upload_complete.connect(self._on_upload_complete)
        self.add_panel("upload", "\u25B2", "\uc5c5\ub85c\ub4dc", self._upload)

        # Analysis
        self._analysis = AnalysisPanel()
        self._analysis.back_to_project.connect(
            self.controller.navigate_project_detail.emit)
        self.add_panel("analysis", "\u25C7", "\ubd84\uc11d", self._analysis)

        self.add_sidebar_separator("\uad00\ub9ac")

        # Project Form
        self._form = ProjectFormPanel()
        self.add_panel("form", "+", "\uc0c8 \ud504\ub85c\uc81d\ud2b8", self._form)

        # Connect form signals
        self._form.saved.connect(self._on_project_saved)
        self._form.cancelled.connect(self._on_form_cancelled)

    def _connect_navigation(self):
        """Wire controller signals to panel switching."""
        self.controller.navigate_dashboard.connect(
            lambda: self._switch_to("dashboard"))
        self.controller.navigate_project_detail.connect(
            self._on_navigate_to_project)
        self.controller.navigate_upload.connect(
            self._on_navigate_to_upload)
        self.controller.navigate_analysis.connect(
            self._on_navigate_to_analysis)
        self.controller.navigate_form_new.connect(
            self._on_navigate_to_new_project)
        self.controller.navigate_form_edit.connect(
            self._on_navigate_to_edit_project)

        # Data change signals
        self.controller.files_changed.connect(self._on_files_changed)

    def _switch_to(self, panel_id: str):
        """Switch to a panel by ID."""
        self.sidebar.set_active_panel(panel_id)

    # ── Navigation handlers ──

    def _on_navigate_to_project(self, project_id: int):
        self._project_detail.load_project(project_id)
        self._switch_to("project_detail")
        project = DataService.get_project(project_id)
        if project:
            self.top_bar.set_title(project["name"])

    def _on_navigate_to_upload(self, project_id: int):
        self._upload.set_project(project_id)
        self._switch_to("upload")

    def _on_navigate_to_analysis(self, file_id: int, project_id: int):
        self._analysis.load_file(file_id, project_id)
        self._switch_to("analysis")
        file_info = DataService.get_file(file_id)
        if file_info:
            self.top_bar.set_title(f"\ud504\ub85c\uc81d\ud2b8 QC \u2014 {file_info['filename']}")

    def _on_navigate_to_new_project(self):
        self._form.clear_form()
        self._switch_to("form")

    def _on_navigate_to_edit_project(self, project_id: int):
        self._form.set_edit_mode(project_id)
        self._switch_to("form")

    # ── Event handlers ──

    def _on_project_saved(self, project_id: int):
        self.controller.project_created.emit(project_id)
        self.controller.show_toast("\ud504\ub85c\uc81d\ud2b8\uac00 \uc800\uc7a5\ub418\uc5c8\uc2b5\ub2c8\ub2e4", "success")
        self._dashboard.refresh()
        self._switch_to("dashboard")

    def _on_form_cancelled(self):
        self._switch_to("dashboard")

    def _on_upload_complete(self, project_id: int):
        self.controller.files_changed.emit(project_id)
        self.controller.show_toast("\ud30c\uc77c \uc5c5\ub85c\ub4dc\uac00 \uc644\ub8cc\ub418\uc5c8\uc2b5\ub2c8\ub2e4", "success")

    def _on_files_changed(self, project_id: int):
        """Refresh project detail if currently viewing it."""
        if self._project_detail.get_project_id() == project_id:
            self._project_detail.refresh()

    def _delete_project(self, project_id: int):
        from PySide6.QtWidgets import QMessageBox
        project = DataService.get_project(project_id)
        if not project:
            return

        reply = QMessageBox.question(
            self, "\ud504\ub85c\uc81d\ud2b8 \uc0ad\uc81c",
            f"'{project['name']}' \ud504\ub85c\uc81d\ud2b8\ub97c \uc0ad\uc81c\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?\n"
            "\ubaa8\ub4e0 \ud30c\uc77c\uacfc \ubd84\uc11d \uacb0\uacfc\uac00 \ud568\uaed8 \uc0ad\uc81c\ub429\ub2c8\ub2e4.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            DataService.delete_project(project_id)
            self.controller.show_toast("\ud504\ub85c\uc81d\ud2b8\uac00 \uc0ad\uc81c\ub418\uc5c8\uc2b5\ub2c8\ub2e4", "success")
            self._dashboard.refresh()
            self._switch_to("dashboard")

    def _show_export_menu(self, project_id: int):
        """Show export format dropdown menu."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: #1A2236;
                color: #D1D5DB;
                border: 1px solid #1F2937;
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: #1E293B;
            }}
        """)

        menu.addAction("Excel (.xlsx)", lambda: self._export_project(project_id, "excel"))
        menu.addAction("Word (.docx)", lambda: self._export_project(project_id, "word"))
        menu.addAction("PPT (.pptx)", lambda: self._export_project(project_id, "ppt"))

        btn = self.sender()
        if btn:
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        else:
            from PySide6.QtGui import QCursor
            menu.exec(QCursor.pos())

    def _export_project(self, project_id: int, fmt: str):
        """Export project report."""
        latest_result = DataService.get_latest_project_result(project_id)
        if not latest_result:
            self.controller.show_toast(
                "\uc644\ub8cc\ub41c QC \uc2a4\ub0c5\uc0f7\uc774 \uc5c6\uc5b4 export\ub97c \ub9cc\ub4e4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4",
                "warning",
            )
            return

        ext_map = {"excel": ".xlsx", "word": ".docx", "ppt": ".pptx"}
        ext = ext_map.get(fmt, ".xlsx")
        filter_map = {
            "excel": "Excel (*.xlsx)",
            "word": "Word (*.docx)",
            "ppt": "PowerPoint (*.pptx)",
        }

        project = DataService.get_project(project_id)
        default_name = f"MBESQC_Report_{project['name']}{ext}" if project else f"MBESQC_Report{ext}"

        path, _ = QFileDialog.getSaveFileName(
            self, "보고서 저장", default_name, filter_map.get(fmt, ""))
        if not path:
            return

        # Preserve old workers for GC
        if self._export_worker:
            self._old_workers.append((self._export_worker, self._export_thread))

        self._export_worker = ExportWorker(project_id, fmt, path)
        self._export_thread = QThread()
        self._export_worker.moveToThread(self._export_thread)

        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.finished.connect(self._export_thread.quit)

        self.controller.show_toast(f"{fmt.upper()} 보고서 생성 중...", "info")
        self._export_thread.start()

    def _on_export_done(self, path: str):
        self.controller.show_toast(
            f"보고서 저장 완료: {os.path.basename(path)}", "success")

    def _on_export_error(self, msg: str):
        self.controller.show_toast(f"보고서 생성 실패: {msg}", "error")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self, self._on_navigate_to_new_project)
        QShortcut(QKeySequence("Ctrl+H"), self, lambda: self._switch_to("dashboard"))
        QShortcut(QKeySequence("F5"), self, self._on_refresh)
        QShortcut(QKeySequence("Escape"), self, self._on_escape)

    def _on_refresh(self):
        """Refresh current panel."""
        panel = self.content_stack.currentWidget()
        if panel == self._dashboard:
            self._dashboard.refresh()
        elif panel == self._project_detail:
            self._project_detail.refresh()

    def _on_escape(self):
        panel = self.content_stack.currentWidget()
        if panel == self._form:
            self._on_form_cancelled()
        elif panel == self._analysis:
            pid = self._analysis.get_project_id()
            if pid:
                self._on_navigate_to_project(pid)
        elif panel == self._project_detail:
            self._switch_to("dashboard")

    # ── TopBar action setup ──

    def _switch_panel(self, panel_id: str):
        """Override to add TopBar actions per panel."""
        super()._switch_panel(panel_id)

        # Clear existing action buttons
        while self.top_bar.actions_layout.count():
            item = self.top_bar.actions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if panel_id == "dashboard":
            self.top_bar.add_action_button("+ \uc0c8 \ud504\ub85c\uc81d\ud2b8",
                                           self._on_navigate_to_new_project,
                                           primary=True)

        elif panel_id == "project_detail":
            pid = self._project_detail.get_project_id()
            if pid:
                self.top_bar.add_action_button("Back",
                    lambda p=pid: self._switch_to("dashboard"))
                self.top_bar.add_action_button("Upload",
                    lambda p=pid: self.controller.navigate_upload.emit(p))
                self.top_bar.add_action_button("\ud504\ub85c\uc81d\ud2b8 QC",
                    lambda: self._project_detail.run_batch_qc())
                self.top_bar.add_action_button("Export",
                    lambda p=pid: self._show_export_menu(p))

        elif panel_id == "upload":
            pid = getattr(self._upload, '_project_id', None)
            if pid:
                self.top_bar.add_action_button("Back",
                    lambda p=pid: self._on_navigate_to_project(p))

        elif panel_id == "analysis":
            pid = self._analysis.get_project_id()
            if pid:
                self.top_bar.add_action_button("Back",
                    lambda p=pid: self._on_navigate_to_project(p))
                self.top_bar.add_action_button("Export",
                    lambda p=pid: self._show_export_menu(p))

        elif panel_id == "form":
            pass  # Form has its own Cancel/Save buttons


def main():
    MBESQCApp.run()


if __name__ == "__main__":
    main()

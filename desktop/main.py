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
from geoview_pyside6.icons import icon
from geoview_pyside6.constants import Dark, Font
from geoview_pyside6.help import set_help

from desktop.app_controller import AppController
from desktop.widgets.toast import ToastManager
from desktop.services.data_service import DataService
from desktop.i18n import install as install_i18n
from desktop.panels.dashboard_panel import DashboardPanel
from desktop.panels.project_detail_panel import ProjectDetailPanel
from desktop.panels.upload_panel import UploadPanel
from desktop.panels.analysis_panel import AnalysisPanel
from desktop.panels.project_form_panel import ProjectFormPanel
from desktop.panels.dqr_panel import DQRPanel
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

        install_i18n(self)
        self.set_language(self.lang_manager.lang, refresh=True)

    def setup_panels(self):
        """Register all 5 panels."""
        # Dashboard
        self._dashboard = DashboardPanel()
        self._dashboard.project_selected.connect(
            self.controller.navigate_project_detail.emit)
        self._dashboard.new_project.connect(
            self.controller.navigate_form_new.emit)
        self.add_panel("dashboard", icon("layout-dashboard"), self.t("sidebar.dashboard", "\ub300\uc2dc\ubcf4\ub4dc"), self._dashboard)

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
        self.add_panel("project_detail", icon("folder-open"), self.t("sidebar.project_detail", "\ud504\ub85c\uc81d\ud2b8"), self._project_detail)

        # Upload
        self._upload = UploadPanel()
        self._upload.upload_complete.connect(self._on_upload_complete)
        self.add_panel("upload", icon("upload"), self.t("sidebar.upload", "\uc5c5\ub85c\ub4dc"), self._upload)

        # Analysis
        self._analysis = AnalysisPanel()
        self._analysis.back_to_project.connect(
            self.controller.navigate_project_detail.emit)
        self.add_panel("analysis", icon("bar-chart-3"), self.t("sidebar.analysis", "\ubd84\uc11d"), self._analysis)

        # DQR (Daily QC Report)
        self._dqr = DQRPanel()
        self._dqr.toast_requested.connect(self.controller.toast_requested.emit)
        self.add_panel("dqr", icon("file-text"), self.t("sidebar.dqr", "DQR"), self._dqr)

        self.add_sidebar_separator(self.t("sidebar.management", "\uad00\ub9ac"))

        # Project Form
        self._form = ProjectFormPanel()
        self.add_panel("form", icon("plus-circle"), self.t("sidebar.form", "\uc0c8 \ud504\ub85c\uc81d\ud2b8"), self._form)

        # Connect form signals
        self._form.saved.connect(self._on_project_saved)
        self._form.cancelled.connect(self._on_form_cancelled)

    # ── Project Context Integration ──

    def on_project_context_changed(self, ctx, old_ctx=None):
        """공유 프로젝트 컨텍스트 변경 시 vessel_config_id + raw_data 힌트."""
        if ctx is None:
            return
        parts = []
        if ctx.vessel_config_id is not None:
            parts.append(self.t("status.om_config", "OM Config: #{value}").format(value=ctx.vessel_config_id))
        if ctx.vessel:
            parts.append(self.t("status.vessel", "Vessel: {value}").format(value=ctx.vessel))
        if ctx.paths and ctx.paths.raw_data:
            parts.append(self.t("status.raw_data", "Raw: {path}").format(path=ctx.paths.raw_data))
        if parts:
            self.status_bar.showMessage(" | ".join(parts), 5000)

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
            self.top_bar.set_title(self.t("status.analysis_prefix", "\ud504\ub85c\uc81d\ud2b8 QC \u2014 {name}").format(name=file_info["filename"]))

    def _on_navigate_to_new_project(self):
        self._form.clear_form()
        self._switch_to("form")

    def _on_navigate_to_edit_project(self, project_id: int):
        self._form.set_edit_mode(project_id)
        self._switch_to("form")

    # ── Event handlers ──

    def _on_project_saved(self, project_id: int):
        self.controller.project_created.emit(project_id)
        self.controller.show_toast(self.t("toast.project_saved", "\ud504\ub85c\uc81d\ud2b8\uac00 \uc800\uc7a5\ub418\uc5c8\uc2b5\ub2c8\ub2e4"), "success")
        self._dashboard.refresh()
        self._switch_to("dashboard")

    def _on_form_cancelled(self):
        self._switch_to("dashboard")

    def _on_upload_complete(self, project_id: int):
        self.controller.files_changed.emit(project_id)
        self.controller.show_toast(self.t("toast.upload_complete", "\ud30c\uc77c \uc5c5\ub85c\ub4dc\uac00 \uc644\ub8cc\ub418\uc5c8\uc2b5\ub2c8\ub2e4"), "success")

    def _on_files_changed(self, project_id: int):
        """Refresh project detail if currently viewing it."""
        if self._project_detail.get_project_id() == project_id:
            self._project_detail.refresh()

    def _delete_project(self, project_id: int):
        project = DataService.get_project(project_id)
        if not project:
            return

        if not self.show_confirm(
            self.t("dialog.project_delete_title", "\ud504\ub85c\uc81d\ud2b8 \uc0ad\uc81c"),
            f"'{project['name']}' \ud504\ub85c\uc81d\ud2b8\ub97c \uc0ad\uc81c\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?\n"
            "\ubaa8\ub4e0 \ud30c\uc77c\uacfc \ubd84\uc11d \uacb0\uacfc\uac00 \ud568\uaed8 \uc0ad\uc81c\ub429\ub2c8\ub2e4.",
            confirm_text="Delete",
        ):
            return
        DataService.delete_project(project_id)
        self.controller.show_toast(self.t("toast.project_deleted", "\ud504\ub85c\uc81d\ud2b8\uac00 \uc0ad\uc81c\ub418\uc5c8\uc2b5\ub2c8\ub2e4"), "success")
        self._dashboard.refresh()
        self._switch_to("dashboard")

    def _show_export_menu(self, project_id: int):
        """Show export format dropdown menu."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {Dark.DARK};
                color: {Dark.TEXT_SEC};
                border: 1px solid {Dark.BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: {Dark.NAVY};
            }}
        """)

        menu.addAction(self.t("menu.export.excel", "Excel (.xlsx)"), lambda: self._export_project(project_id, "excel"))
        menu.addAction(self.t("menu.export.word", "Word (.docx)"), lambda: self._export_project(project_id, "word"))
        menu.addAction(self.t("menu.export.ppt", "PPT (.pptx)"), lambda: self._export_project(project_id, "ppt"))

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
                self.t("toast.export_preview_missing", "\uc644\ub8cc\ub41c QC \uc2a4\ub0c5\uc0f7\uc774 \uc5c6\uc5b4 export\ub97c \ub9cc\ub4e4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4"),
                "warning",
            )
            return

        ext_map = {"excel": ".xlsx", "word": ".docx", "ppt": ".pptx"}
        ext = ext_map.get(fmt, ".xlsx")
        filter_map = {
            "excel": self.t("filefilter.excel", "Excel (*.xlsx)"),
            "word": self.t("filefilter.word", "Word (*.docx)"),
            "ppt": self.t("filefilter.ppt", "PowerPoint (*.pptx)"),
        }

        project = DataService.get_project(project_id)
        default_name = (
            self.t("save.report.default", "MBESQC_Report_{project}").format(project=project["name"]) + ext
            if project else self.t("save.report.default_fallback", "MBESQC_Report") + ext
        )

        path, _ = QFileDialog.getSaveFileName(
            self, self.t("dialog.export_save_title", "보고서 저장"), default_name, filter_map.get(fmt, ""))
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
        self._export_worker.error.connect(self._export_thread.quit)

        self.controller.show_toast(self.t("toast.export_running", "{fmt} 보고서 생성 중...").format(fmt=fmt.upper()), "info")
        self._export_thread.start()

    def _on_export_done(self, path: str):
        self.controller.show_toast(
            self.t("toast.export_saved", "보고서 저장 완료: {name}").format(name=os.path.basename(path)), "success")

    def _on_export_error(self, msg: str):
        self.controller.show_toast(self.t("toast.export_failed", "보고서 생성 실패: {error}").format(error=msg), "error")

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
            btn = self.top_bar.add_action_button(self.t("action.new_project", "+ \uc0c8 \ud504\ub85c\uc81d\ud2b8"),
                                           self._on_navigate_to_new_project,
                                           primary=True)
            set_help(btn, "Create a new MBES QC project")

        elif panel_id == "project_detail":
            pid = self._project_detail.get_project_id()
            if pid:
                btn = self.top_bar.add_action_button(self.t("action.back", "Back"),
                    lambda p=pid: self._switch_to("dashboard"))
                set_help(btn, "Return to dashboard")
                btn = self.top_bar.add_action_button(self.t("action.upload", "Upload"),
                    lambda p=pid: self.controller.navigate_upload.emit(p))
                set_help(btn, "Upload MBES data files to this project")
                btn = self.top_bar.add_action_button(self.t("action.project_qc", "\ud504\ub85c\uc81d\ud2b8 QC"),
                    lambda: self._project_detail.run_batch_qc())
                set_help(btn, "Run QC analysis on all files at once")
                btn = self.top_bar.add_action_button(self.t("action.export", "Export"),
                    lambda p=pid: self._show_export_menu(p))
                set_help(btn, "Export QC results to Excel/Word/PPT")

        elif panel_id == "upload":
            pid = getattr(self._upload, '_project_id', None)
            if pid:
                btn = self.top_bar.add_action_button(self.t("action.back", "Back"),
                    lambda p=pid: self._on_navigate_to_project(p))
                set_help(btn, "Return to project detail")

        elif panel_id == "analysis":
            pid = self._analysis.get_project_id()
            if pid:
                btn = self.top_bar.add_action_button(self.t("action.back", "Back"),
                    lambda p=pid: self._on_navigate_to_project(p))
                set_help(btn, "Return to project detail")
                btn = self.top_bar.add_action_button(self.t("action.export", "Export"),
                    lambda p=pid: self._show_export_menu(p))
                set_help(btn, "Export QC results to Excel/Word/PPT")

        elif panel_id == "form":
            pass  # Form has its own Cancel/Save buttons

    def on_language_changed(self, lang: str, force: bool = False) -> None:
        label_map = {
            "dashboard": self.t("sidebar.dashboard", "대시보드"),
            "project_detail": self.t("sidebar.project_detail", "프로젝트"),
            "upload": self.t("sidebar.upload", "업로드"),
            "analysis": self.t("sidebar.analysis", "분석"),
            "dqr": self.t("sidebar.dqr", "DQR"),
            "form": self.t("sidebar.form", "새 프로젝트"),
        }
        for btn in getattr(self.sidebar, "buttons", []):
            btn.setText(label_map.get(btn.panel_id, btn.text()))
        self.sidebar.set_static_text(
            nav_header=self.t("sidebar.nav_header", "MENU"),
            status_text=self.t("sidebar.ready", "Ready"),
            separators=[self.t("sidebar.management", "관리")],
        )

        for panel, key, default in [
            (self._dashboard, "panel.dashboard", "대시보드"),
            (self._project_detail, "panel.project_detail", "프로젝트"),
            (self._upload, "panel.upload", "업로드"),
            (self._analysis, "panel.analysis", "분석"),
            (self._dqr, "panel.dqr", "DQR"),
            (self._form, "panel.form", "새 프로젝트"),
        ]:
            if hasattr(panel, "panel_title"):
                panel.panel_title = self.t(key, default)

        if getattr(self, "_current_panel", None):
            self._switch_panel(self._current_panel)


def main():
    MBESQCApp.run()


if __name__ == "__main__":
    main()

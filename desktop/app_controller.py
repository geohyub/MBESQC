"""MBESQC AppController -- Central navigation and state management.

Decouples panels from each other. All inter-panel communication
goes through this controller via Qt signals.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AppController(QObject):
    """Central controller for panel navigation and app-wide state."""

    # Navigation signals
    navigate_dashboard = Signal()
    navigate_project_detail = Signal(int)       # project_id
    navigate_upload = Signal(int)               # project_id
    navigate_analysis = Signal(int, int)        # file_id, project_id
    navigate_form_new = Signal()
    navigate_form_edit = Signal(int)            # project_id

    # Data change signals (for refresh)
    project_created = Signal(int)               # project_id
    project_updated = Signal(int)               # project_id
    project_deleted = Signal(int)               # project_id
    files_changed = Signal(int)                 # project_id
    analysis_complete = Signal(int, int)        # file_id, project_id

    # Toast signal
    toast_requested = Signal(str, str)          # message, level

    def __init__(self, parent=None):
        super().__init__(parent)

    def show_toast(self, message: str, level: str = "info"):
        """Emit toast notification."""
        self.toast_requested.emit(message, level)

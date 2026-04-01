"""MBESQC Toast -- Non-blocking notification widget (bottom-right of content area)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QGraphicsOpacityEffect

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Dark, Font, Space, Radius


_TOAST_COLORS = {
    "success": (Dark.GREEN, f"rgba(16,185,129,0.12)"),
    "warning": (Dark.ORANGE, f"rgba(245,158,11,0.12)"),
    "error":   (Dark.RED, f"rgba(239,68,68,0.12)"),
    "info":    (Dark.BLUE, f"rgba(59,130,246,0.12)"),
}

_TOAST_DURATIONS = {
    "success": 3000,
    "warning": 5000,
    "error":   8000,
    "info":    3000,
}


class ToastWidget(QWidget):
    def __init__(self, message: str, level: str = "info", parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setMinimumWidth(260)
        self.setMaximumWidth(460)

        accent, _ = _TOAST_COLORS.get(level, _TOAST_COLORS["info"])

        self.setStyleSheet(f"""
            ToastWidget {{
                background: {Dark.NAVY};
                border: 1px solid {Dark.BORDER};
                border-left: 3px solid {accent};
                border-radius: {Radius.SM}px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        msg_label = QLabel(message)
        msg_label.setStyleSheet(f"""
            color: {Dark.TEXT};
            font-size: {Font.SM}px;
            background: transparent;
            border: none;
        """)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label, 1)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._anim_in = QPropertyAnimation(self._opacity, b"opacity")
        self._anim_in.setDuration(200)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_in.start()

        duration = _TOAST_DURATIONS.get(level, 3000)
        QTimer.singleShot(duration, self._fade_out)

    def _fade_out(self):
        self._anim_out = QPropertyAnimation(self._opacity, b"opacity")
        self._anim_out.setDuration(300)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(QEasingCurve.InCubic)
        self._anim_out.finished.connect(self._remove)
        self._anim_out.start()

    def _remove(self):
        if self.parent():
            self.setParent(None)
        self.deleteLater()


class ToastManager:
    _instance = None

    def __init__(self, parent_window):
        self._parent = parent_window
        self._toasts = []
        ToastManager._instance = self

    @classmethod
    def instance(cls):
        return cls._instance

    def show_toast(self, message: str, level: str = "info"):
        # Limit to 3 visible toasts
        while len(self._toasts) >= 3:
            oldest = self._toasts.pop(0)
            oldest._fade_out()

        toast = ToastWidget(message, level, self._parent)
        self._toasts.append(toast)
        toast.destroyed.connect(
            lambda: self._toasts.remove(toast) if toast in self._toasts else None)
        self._position_toasts()
        toast.show()
        toast.raise_()

    def _position_toasts(self):
        parent = self._parent
        margin = 16
        y_offset = margin

        for toast in reversed(self._toasts):
            if not toast.isVisible() and toast.isHidden():
                continue
            toast.adjustSize()
            x = parent.width() - toast.width() - margin
            y = parent.height() - toast.height() - y_offset
            toast.move(max(x, 0), max(y, 0))
            y_offset += toast.height() + 8

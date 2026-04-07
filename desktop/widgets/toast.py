"""MBESQC Toast -- Non-blocking notification with slide-in animation (bottom-right)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QGraphicsOpacityEffect

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c


_TOAST_DURATIONS = {
    "success": 3000,
    "warning": 5000,
    "error":   8000,
    "info":    3000,
}

_SLIDE_OFFSET = 20  # pixels to slide in from right


class ToastWidget(QWidget):
    def __init__(self, message: str, level: str = "info", parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setMinimumWidth(260)
        self.setMaximumWidth(460)

        accent_map = {
            "success": c().GREEN,
            "warning": c().ORANGE,
            "error":   c().RED,
            "info":    c().BLUE,
        }
        accent = accent_map.get(level, c().BLUE)

        # c() based theming: NAVY background, accent left border
        self.setStyleSheet(f"""
            ToastWidget {{
                background: {c().NAVY};
                border: 1px solid {accent};
                border-left: 3px solid {accent};
                border-radius: {Radius.SM}px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        msg_label = QLabel(message)
        msg_label.setStyleSheet(f"""
            color: {c().TEXT};
            font-size: {Font.SM}px;
            background: transparent;
            border: none;
        """)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label, 1)

        # Opacity effect for fade
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # Store target position for slide-in (set by manager)
        self._target_pos = QPoint(0, 0)

        # Fade-in animation
        self._anim_in = QPropertyAnimation(self._opacity, b"opacity")
        self._anim_in.setDuration(200)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(QEasingCurve.OutCubic)

        # Slide-in animation (will be configured by manager)
        self._slide_in = QPropertyAnimation(self, b"pos")
        self._slide_in.setDuration(200)
        self._slide_in.setEasingCurve(QEasingCurve.OutCubic)

        duration = _TOAST_DURATIONS.get(level, 3000)
        QTimer.singleShot(duration, self._fade_out)

    def start_entrance(self, target_pos: QPoint):
        """Start slide-in + fade-in from offset position."""
        self._target_pos = target_pos
        start_pos = QPoint(target_pos.x() + _SLIDE_OFFSET, target_pos.y())
        self.move(start_pos)

        self._slide_in.setStartValue(start_pos)
        self._slide_in.setEndValue(target_pos)
        self._slide_in.start()
        self._anim_in.start()

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
        toast.show()
        toast.raise_()
        self._position_toasts()

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
            target = QPoint(max(x, 0), max(y, 0))
            toast.start_entrance(target)
            y_offset += toast.height() + 8

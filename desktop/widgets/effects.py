"""MBESQC animation effects -- reusable helpers for widget animations.

Provides:
- shake_widget(): horizontal shake effect (3px, 3 cycles, ~200ms)
- pulse_opacity(): infinite opacity pulse (1.0 -> 0.5 -> 1.0, 2s period)
- stop_pulse(): stop a running pulse animation and restore full opacity
- fade_in(): opacity 0.5 -> 1.0 fade (300ms)
- accent_glow(): temporary drop-shadow glow, auto-removed after duration
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget


def shake_widget(widget: QWidget, amplitude: int = 3, cycles: int = 3,
                 duration_ms: int = 200) -> QSequentialAnimationGroup:
    """Apply a horizontal shake effect to *widget*.

    Returns the animation group so the caller can track lifetime.
    The widget's geometry is restored after the shake completes.
    """
    group = QSequentialAnimationGroup(widget)
    per_cycle = max(duration_ms // (cycles * 2), 10)

    original_x = widget.x()

    for _ in range(cycles):
        # Left
        anim_left = QPropertyAnimation(widget, b"pos", widget)
        anim_left.setDuration(per_cycle)
        anim_left.setStartValue(widget.pos())
        anim_left.setEndValue(widget.pos().__class__(original_x - amplitude, widget.y()))
        anim_left.setEasingCurve(QEasingCurve.InOutSine)
        group.addAnimation(anim_left)

        # Right
        anim_right = QPropertyAnimation(widget, b"pos", widget)
        anim_right.setDuration(per_cycle)
        anim_right.setStartValue(widget.pos().__class__(original_x - amplitude, widget.y()))
        anim_right.setEndValue(widget.pos().__class__(original_x + amplitude, widget.y()))
        anim_right.setEasingCurve(QEasingCurve.InOutSine)
        group.addAnimation(anim_right)

    # Return to center
    anim_back = QPropertyAnimation(widget, b"pos", widget)
    anim_back.setDuration(per_cycle)
    anim_back.setEndValue(widget.pos().__class__(original_x, widget.y()))
    anim_back.setEasingCurve(QEasingCurve.OutSine)
    group.addAnimation(anim_back)

    group.start()
    return group


def _ensure_opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    """Get or create an opacity effect on *widget*."""
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)
    return effect


def pulse_opacity(widget: QWidget, low: float = 0.5, high: float = 1.0,
                  period_ms: int = 2000) -> QPropertyAnimation:
    """Infinite opacity pulse on *widget*.

    Returns the QPropertyAnimation so the caller can stop it later.
    """
    effect = _ensure_opacity_effect(widget)
    half = period_ms // 2

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(half)
    anim.setStartValue(high)
    anim.setEndValue(low)
    anim.setEasingCurve(QEasingCurve.InOutSine)
    anim.setLoopCount(-1)  # infinite -- toggles direction automatically

    # Use a sequential group to get ping-pong (down then up)
    # QPropertyAnimation with loopCount=-1 replays the same direction,
    # so wrap in a sequential group with two animations.
    from PySide6.QtCore import QSequentialAnimationGroup as SeqGroup

    group = SeqGroup(widget)

    anim_down = QPropertyAnimation(effect, b"opacity", widget)
    anim_down.setDuration(half)
    anim_down.setStartValue(high)
    anim_down.setEndValue(low)
    anim_down.setEasingCurve(QEasingCurve.InOutSine)

    anim_up = QPropertyAnimation(effect, b"opacity", widget)
    anim_up.setDuration(half)
    anim_up.setStartValue(low)
    anim_up.setEndValue(high)
    anim_up.setEasingCurve(QEasingCurve.InOutSine)

    group.addAnimation(anim_down)
    group.addAnimation(anim_up)
    group.setLoopCount(-1)
    group.start()

    return group  # type: ignore[return-value]


def stop_pulse(widget: QWidget, anim_group) -> None:
    """Stop a running pulse animation and restore full opacity."""
    if anim_group is not None:
        anim_group.stop()
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        effect.setOpacity(1.0)


def fade_in(widget: QWidget, duration_ms: int = 300,
            start: float = 0.5, end: float = 1.0) -> QPropertyAnimation:
    """Opacity fade-in on *widget*."""
    effect = _ensure_opacity_effect(widget)
    effect.setOpacity(start)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start()
    return anim


def accent_glow(widget: QWidget, color_hex: str, blur: int = 18,
                duration_ms: int = 1000) -> None:
    """Apply a temporary drop-shadow glow, auto-removed after *duration_ms*."""
    from PySide6.QtGui import QColor

    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, 0)
    shadow.setColor(QColor(color_hex))
    widget.setGraphicsEffect(shadow)

    def _remove():
        # Only remove if the effect is still ours
        current = widget.graphicsEffect()
        if current is shadow:
            widget.setGraphicsEffect(None)

    QTimer.singleShot(duration_ms, _remove)

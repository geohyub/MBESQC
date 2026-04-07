"""MBESQC QCUnlockGrid -- 9-module QC availability grid based on available file types."""

from __future__ import annotations

import os
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QSizePolicy,
    QWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "_shared"))
from geoview_pyside6.constants import Font, Space, Radius
from geoview_pyside6.theme_aware import c

from desktop.widgets.effects import fade_in, accent_glow, pulse_opacity, stop_pulse


# QC module definitions: (id, name, description, requirements)
QC_MODULES = [
    ("preprocess", "Pre-Processing", "처리 전 설정/항법/메타데이터 게이트", ["pds"]),
    ("file",      "File QC",      "파일 무결성/네이밍/시간 연속성",     ["pds"]),
    ("vessel",    "Vessel QC",    "PDS vs HVF 오프셋 비교",            ["pds"]),
    ("offset",    "Offset QC",    "Roll/Pitch bias 추정",              ["gsf"]),
    ("motion",    "Motion QC",    "자세 스파이크/갭/드리프트",          ["gsf"]),
    ("svp",       "SVP QC",       "음속 프로파일 적용 확인",            ["gsf"]),
    ("coverage",  "Coverage QC",  "스와스 커버리지/오버랩",             ["gsf_multi"]),
    ("crossline", "Cross-line QC","IHO S-44 깊이 차이",                ["gsf_multi"]),
    ("surface",   "Surface",      "DTM/Density/Std 그리딩",            ["gsf"]),
]


def _score_color(score: float) -> str:
    """Return color based on score threshold."""
    if score >= 80:
        return c().GREEN
    elif score >= 50:
        return c().ORANGE
    return c().RED


def _tint_bg(base_hex: str, tint_hex: str, alpha: float) -> str:
    """Blend a tint color onto a base at given alpha.

    Returns rgba() CSS for the tinted background.
    """
    # Parse tint hex to r,g,b
    tint = tint_hex.lstrip("#")
    tr, tg, tb = int(tint[:2], 16), int(tint[2:4], 16), int(tint[4:6], 16)
    # Parse base hex
    base = base_hex.lstrip("#")
    br, bg_, bb = int(base[:2], 16), int(base[2:4], 16), int(base[4:6], 16)
    # Blend
    r = int(br * (1 - alpha) + tr * alpha)
    g = int(bg_ * (1 - alpha) + tg * alpha)
    b = int(bb * (1 - alpha) + tb * alpha)
    return f"rgb({r}, {g}, {b})"


class _ScoreBar(QWidget):
    """Thin horizontal score bar (4px height) with fill proportional to score."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0.0
        self._fill_color = c().BORDER
        self.setFixedHeight(4)

    def set_score(self, score: float, fill_color: str):
        self._score = max(0.0, min(100.0, score))
        self._fill_color = fill_color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        r = h / 2

        # Background track
        bg_color = QColor(c().BORDER)
        painter.setBrush(bg_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, w, h, r, r)

        # Fill
        if self._score > 0:
            fill_w = max(int(w * self._score / 100.0), h)  # min width = height for rounding
            fill_color = QColor(self._fill_color)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(0, 0, fill_w, h, r, r)

        painter.end()

    def refresh_theme(self):
        self.update()


class _QCModuleCard(QFrame):
    """Individual QC module status card with score bar and result details."""

    def __init__(self, qc_id: str, name: str, description: str, parent=None):
        super().__init__(parent)
        self._qc_id = qc_id
        self._name = name
        self._state = "locked"  # locked / unlocked / done / running / error
        self._flashing = False
        self._score = 0.0
        self._status = "LOCKED"
        self._key_metric = ""
        self._issue = ""
        self._weight = 0
        self._pulse_anim = None  # warning/fail pulse animation reference
        self._fade_anim = None   # unlock fade animation reference
        self.setObjectName(f"qccard_{qc_id}")

        self.setFixedHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        layout.setSpacing(3)

        # Row 1: Name (bold) + weight (muted, right)
        header = QHBoxLayout()
        header.setSpacing(4)

        self._name_label = QLabel(name)
        self._name_label.setObjectName("qccard_name")
        header.addWidget(self._name_label)
        header.addStretch()

        self._weight_label = QLabel("")
        self._weight_label.setObjectName("qccard_weight")
        header.addWidget(self._weight_label)
        layout.addLayout(header)

        # Row 2: Score bar + score number
        bar_row = QHBoxLayout()
        bar_row.setSpacing(6)

        self._score_bar = _ScoreBar()
        bar_row.addWidget(self._score_bar, 1)

        self._score_label = QLabel("")
        self._score_label.setObjectName("qccard_score")
        self._score_label.setFixedWidth(28)
        self._score_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar_row.addWidget(self._score_label)
        layout.addLayout(bar_row)

        # Row 3: Key metric
        self._metric_label = QLabel("")
        self._metric_label.setObjectName("qccard_metric")
        self._metric_label.setWordWrap(True)
        layout.addWidget(self._metric_label)

        # Row 4: Status badge
        self._status_label = QLabel()
        self._status_label.setObjectName("qccard_status")
        layout.addWidget(self._status_label)

        self._apply_state()

    # ------------------------------------------------------------------
    # Animation: unlock transition
    # ------------------------------------------------------------------

    def _play_unlock_animation(self):
        """Animate: opacity fade-in + 2px lift + glow."""
        # 1) Opacity 0.5 -> 1.0 fade (300ms)
        self._fade_anim = fade_in(self, duration_ms=300, start=0.5, end=1.0)

        # 2) 2px upward lift via contentsMargins adjustment
        layout = self.layout()
        if layout:
            m = layout.contentsMargins()
            # Shrink top margin by 2px to create visual "lift"
            orig_top = m.top()
            layout.setContentsMargins(m.left(), max(0, orig_top - 2), m.right(), m.bottom() + 2)

        # 3) Accent glow -- auto-removed after 1 second
        accent_glow(self, c().CYAN, blur=18, duration_ms=1000)

    # ------------------------------------------------------------------
    # Animation: warning/fail status pulse
    # ------------------------------------------------------------------

    def _start_pulse(self):
        """Start infinite opacity pulse on the status label for WARNING/FAIL."""
        if self._pulse_anim is not None:
            return  # already pulsing
        self._pulse_anim = pulse_opacity(self._status_label, low=0.5, high=1.0, period_ms=2000)

    def _stop_pulse(self):
        """Stop any running pulse animation on the status label."""
        if self._pulse_anim is not None:
            stop_pulse(self._status_label, self._pulse_anim)
            self._pulse_anim = None

    # ------------------------------------------------------------------

    def set_state(self, state: str):
        old_state = self._state
        self._state = state
        self._apply_state()

        # Unlock animation: locked -> unlocked
        if old_state == "locked" and state == "unlocked":
            self._play_unlock_animation()

        # Flash on state change to done/running/error
        if state != old_state and state in ("done", "running", "error"):
            self._flash()

    def set_result(self, score: float, status: str, key_metric: str = "",
                   issue: str = "", weight: int = 0):
        """Update card with QC result data."""
        self._score = score
        self._status = status.upper() if status else "LOCKED"
        self._key_metric = key_metric
        self._issue = issue
        self._weight = weight

        # Derive state from status
        status_upper = self._status
        if status_upper == "PASS":
            self._state = "done"
        elif status_upper == "WARNING":
            self._state = "done"
        elif status_upper == "FAIL":
            self._state = "error"
        elif status_upper in ("LOCKED", ""):
            self._state = "locked"
        else:
            self._state = "unlocked"

        self._apply_state()

    def _flash(self):
        """Brief 200ms highlight flash for selection feedback."""
        if self._flashing:
            return
        self._flashing = True
        self.setStyleSheet(f"""
            #{self.objectName()} {{
                background: {c().SLATE};
                border: 1px solid {c().CYAN};
                border-radius: {Radius.SM}px;
            }}
        """)
        QTimer.singleShot(200, self._end_flash)

    def _end_flash(self):
        self._flashing = False
        self._apply_state()

    def _apply_state(self):
        if self._flashing:
            return

        status = self._status
        score = self._score
        is_locked = self._state == "locked" or status == "LOCKED"

        # -- Card background --
        bg = c().DARK
        border_color = c().BORDER
        if not is_locked:
            if status == "PASS":
                bg = c().DARK
                border_color = c().GREEN
            elif status == "WARNING":
                bg = _tint_bg(c().DARK, c().ORANGE, 0.05)
                border_color = c().ORANGE
            elif status == "FAIL":
                bg = _tint_bg(c().DARK, c().RED, 0.05)
                border_color = c().RED
            elif self._state == "running":
                border_color = c().BLUE
            elif self._state == "unlocked":
                border_color = c().CYAN

        self.setStyleSheet(f"""
            #{self.objectName()} {{
                background: {bg};
                border: 1px solid {border_color};
                border-radius: {Radius.SM}px;
            }}
            #{self.objectName()}:hover {{
                background: {c().SLATE};
            }}
        """)

        # -- Name --
        dim = is_locked
        self._name_label.setStyleSheet(f"""
            font-size: {Font.SM}px;
            font-weight: {Font.SEMIBOLD};
            color: {c().MUTED if dim else c().TEXT};
            background: transparent;
        """)

        # -- Weight --
        if self._weight > 0:
            self._weight_label.setText(f"{self._weight}pt")
            self._weight_label.setVisible(True)
        else:
            self._weight_label.setVisible(False)
        self._weight_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().MUTED};
            background: transparent;
        """)

        # -- Score bar --
        if not is_locked and score > 0:
            bar_color = _score_color(score)
            self._score_bar.set_score(score, bar_color)
            self._score_bar.setVisible(True)
            self._score_label.setText(f"{score:.0f}")
            self._score_label.setStyleSheet(f"""
                font-size: {Font.XS}px;
                font-weight: {Font.SEMIBOLD};
                color: {bar_color};
                background: transparent;
            """)
            self._score_label.setVisible(True)
        else:
            self._score_bar.setVisible(False)
            self._score_label.setVisible(False)

        # -- Key metric --
        if self._key_metric and not is_locked:
            self._metric_label.setText(self._key_metric)
            self._metric_label.setVisible(True)
        else:
            self._metric_label.setVisible(False)
        self._metric_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            color: {c().TEXT};
            background: transparent;
        """)

        # -- Status badge --
        status_config = {
            "PASS":    ("V PASS",    c().GREEN),
            "WARNING": ("! WARNING", c().ORANGE),
            "FAIL":    ("X FAIL",    c().RED),
            "LOCKED":  ("o LOCKED",  c().MUTED),
            "READY":   ("V READY",   c().CYAN),
            "RUNNING": ("* RUNNING", c().BLUE),
            "N/A":     ("- N/A",     c().MUTED),
        }

        # Map internal state to display
        if is_locked:
            display_status = "LOCKED"
        elif self._state == "running":
            display_status = "RUNNING"
        elif self._state == "unlocked" and status not in ("PASS", "WARNING", "FAIL"):
            display_status = "READY"
        else:
            display_status = status

        badge_text, badge_color = status_config.get(
            display_status, ("\u2014 ---", c().MUTED))

        self._status_label.setText(badge_text)
        self._status_label.setStyleSheet(f"""
            font-size: {Font.XS}px;
            font-weight: {Font.SEMIBOLD};
            color: {badge_color};
            background: transparent;
        """)

        # -- Pulse animation for WARNING/FAIL --
        if display_status in ("WARNING", "FAIL"):
            self._start_pulse()
        else:
            self._stop_pulse()

    def refresh_theme(self):
        """Re-apply theme-dependent colours."""
        self._apply_state()
        self._score_bar.refresh_theme()


class QCUnlockGrid(QFrame):
    """9-module QC availability grid. Updates based on file availability."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QCUnlockGrid {{
                background: transparent;
                border: none;
            }}
        """)

        self._grid = QGridLayout(self)
        self._grid.setSpacing(Space.SM)
        self._grid.setContentsMargins(0, 0, 0, 0)

        self._cards: dict[str, _QCModuleCard] = {}

        for i, (qc_id, name, desc, _reqs) in enumerate(QC_MODULES):
            card = _QCModuleCard(qc_id, name, desc)
            row, col = divmod(i, 4)
            self._grid.addWidget(card, row, col)
            self._cards[qc_id] = card

    def update_availability(self, has_pds: bool, has_gsf: bool,
                            gsf_count: int = 0, has_hvf: bool = False,
                            has_om: bool = False):
        """Update QC module states based on available file types.

        Cards are unlocked in a cascade (80ms staggered, top-left to bottom-right).
        Cards that already have result data (done/error) are not overwritten.
        """
        cascade_index = 0
        for qc_id, _name, _desc, reqs in QC_MODULES:
            available = True
            for req in reqs:
                if req == "pds" and not has_pds:
                    available = False
                elif req == "gsf" and not has_gsf:
                    available = False
                elif req == "gsf_multi" and gsf_count < 2:
                    available = False

            card = self._cards[qc_id]

            # Skip cards that already have QC results -- don't overwrite done/error
            if card._state in ("done", "error"):
                continue

            new_state = "unlocked" if available else "locked"

            if new_state == "unlocked" and card._state == "locked":
                # Staggered unlock: 80ms per card, top-left to bottom-right
                delay = cascade_index * 80
                QTimer.singleShot(delay, lambda c=card: c.set_state("unlocked") if c._state == "locked" else None)
                cascade_index += 1
            else:
                card.set_state(new_state)

    def update_module_results(self, result_data: dict, weights: dict | None = None):
        """Update all module cards with QC result data.

        Parameters
        ----------
        result_data : dict
            Full QC result dict keyed by module id.
        weights : dict | None
            QC_WEIGHTS mapping {qc_id: int}. If None, weights are not displayed.
        """
        for qc_id, card in self._cards.items():
            section = result_data.get(qc_id, {})
            if not section:
                continue

            status = section.get("overall", section.get("verdict", "N/A")).upper()
            score = section.get("score", 0.0)
            weight = (weights or {}).get(qc_id, 0)
            key_metric = self._extract_key_metric(qc_id, section)
            issue = section.get("issue", "")

            card.set_result(
                score=score,
                status=status,
                key_metric=key_metric,
                issue=issue,
                weight=weight,
            )

    def _extract_key_metric(self, qc_id: str, section: dict) -> str:
        """Build a human-readable key metric string from section data."""
        items = section.get("items", [])

        if qc_id == "preprocess":
            total = len(items) if items else 0
            passed = sum(1 for it in (items or []) if str(it.get("status", "")).upper() == "PASS")
            if total > 0:
                return f"게이트 {passed}/{total} 통과"

        elif qc_id == "file":
            total = len(items) if items else 0
            ok = sum(1 for it in (items or []) if str(it.get("status", "")).upper() == "PASS")
            if total > 0:
                return f"무결성 {ok}/{total}"

        elif qc_id == "vessel":
            mismatch = sum(1 for it in (items or []) if str(it.get("status", "")).upper() != "PASS")
            return f"오프셋 불일치 {mismatch}건"

        elif qc_id == "offset":
            roll = section.get("roll_bias_deg", section.get("roll_bias", None))
            if roll is not None:
                return f"Roll bias {roll:.2f}\u00B0"

        elif qc_id == "motion":
            spikes = section.get("spike_count", section.get("total_spikes", None))
            if spikes is not None:
                return f"스파이크 {spikes}개"

        elif qc_id == "svp":
            count = section.get("profile_count", section.get("n_profiles", None))
            if count is not None:
                return f"프로파일 {count}개 적용"

        elif qc_id == "coverage":
            pct = section.get("coverage_pct", section.get("overall_coverage", None))
            if pct is not None:
                return f"커버리지 {pct:.1f}%"

        elif qc_id == "crossline":
            mean_diff = section.get("mean_depth_diff_m", section.get("mean_diff", None))
            if mean_diff is not None:
                return f"깊이 차이 평균 {mean_diff:.3f}m"

        elif qc_id == "surface":
            res = section.get("grid_resolution_m", section.get("resolution", None))
            if res is not None:
                return f"그리드 해상도 {res:.1f}m"

        return ""

    def set_module_state(self, qc_id: str, state: str):
        """Set individual module state (done, running, error)."""
        if qc_id in self._cards:
            self._cards[qc_id].set_state(state)

    def refresh_theme(self):
        """Re-apply theme-dependent colours on all cards."""
        for card in self._cards.values():
            card.refresh_theme()

    def reset_all(self):
        """Reset all modules to locked."""
        for card in self._cards.values():
            card._stop_pulse()
            card.set_state("locked")

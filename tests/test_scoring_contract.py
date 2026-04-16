from __future__ import annotations

import os
import sys


_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SHARED = os.path.join(_ROOT, "..", "..", "_shared")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SHARED)

from desktop.services.analysis_service import compute_score


def test_compute_score_uses_weighted_verdicts_only_for_scored_sections():
    score, grade = compute_score(
        {
            "coverage": {"verdict": "PASS"},
            "crossline": {"verdict": "WARNING"},
            "surface": {"verdict": "FAIL"},
        }
    )

    expected = round(((15 * 1.0) + (20 * 0.7) + (10 * 0.0)) / (15 + 20 + 10) * 100.0, 1)
    assert score == expected
    assert grade == "C"


def test_compute_score_excludes_na_sections_from_denominator():
    score, grade = compute_score(
        {
            "coverage": {"verdict": "PASS"},
            "crossline": {"verdict": "N/A"},
            "surface": {"verdict": "---"},
        }
    )

    assert score == 100.0
    assert grade == "A"


def test_compute_score_returns_fail_closed_for_empty_payload():
    score, grade = compute_score({})

    assert score == 0.0
    assert grade == "F"


def test_compute_score_grade_boundaries_are_exact():
    assert compute_score({"coverage": {"verdict": "PASS"}}) == (100.0, "A")
    assert compute_score({"coverage": {"verdict": "WARNING"}}) == (70.0, "C")
    assert compute_score({"coverage": {"verdict": "FAIL"}}) == (0.0, "F")


def test_compute_score_handles_partial_payload_without_crashing():
    score, grade = compute_score(
        {
            "motion": {"verdict": "WARNING"},
            "offset": {},
            "svp": {"verdict": ""},
        }
    )

    assert score == 70.0
    assert grade == "C"

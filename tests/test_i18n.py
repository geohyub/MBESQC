"""MBESQC bilingual string registry smoke test."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "..", "_shared"))

from geoview_pyside6.i18n import LanguageManager
from desktop.i18n import TRANSLATIONS


def test_mbesqc_translations_cover_shell_labels():
    lm = LanguageManager()
    lm.register(TRANSLATIONS)
    assert lm.t("sidebar.dashboard") == "현황"
    lm.set_lang("en")
    assert lm.t("sidebar.dashboard") == "Overview"
    assert lm.t("menu.export.ppt") == "PowerPoint (.pptx)"


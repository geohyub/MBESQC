"""IHO S-44 Ed.6 Standards for hydrographic survey accuracy.

Provides allowable THU/TVU calculations per survey order.
"""

from __future__ import annotations

import math

# IHO S-44 Ed.6 parameters: (a, b) for TVU = sqrt(a² + (b×d)²)
# a = constant depth error (m), b = depth-dependent factor
IHO_TVU = {
    "exclusive": (0.15, 0.0075),  # Ed.6 (2020)
    "special":   (0.25, 0.0075),
    "1a":        (0.50, 0.0130),
    "1b":        (0.50, 0.0130),
    "2":         (1.00, 0.0230),
}

# THU allowable (metres)
IHO_THU = {
    "exclusive": 1.0,
    "special":   2.0,
    "1a":        5.0,
    "1b":        5.0,
    "2":         20.0,
}


def tvu_allowable(depth: float, order: str = "1a") -> float:
    """Calculate allowable TVU (95% confidence) for given depth and order."""
    a, b = IHO_TVU.get(order.lower(), IHO_TVU["1a"])
    return math.sqrt(a ** 2 + (b * depth) ** 2)


def thu_allowable(order: str = "1a") -> float:
    """Return allowable THU for given order."""
    return IHO_THU.get(order.lower(), IHO_THU["1a"])


def check_tvu(depth: float, tvu: float, order: str = "1a") -> str:
    """Check if TVU meets the specified order."""
    limit = tvu_allowable(depth, order)
    return "PASS" if tvu <= limit else "FAIL"


def check_thu(thu: float, order: str = "1a") -> str:
    """Check if THU meets the specified order."""
    limit = thu_allowable(order)
    return "PASS" if thu <= limit else "FAIL"

"""OffsetManager REST API Client (port 5302).

Provides read-only access to OffsetManager vessel configs and sensor offsets.
Falls back gracefully if OffsetManager is not running.
"""

from __future__ import annotations

import requests

OM_BASE_URL = "http://localhost:5302"
_TIMEOUT = 2


class OMClient:
    """Lightweight HTTP client for OffsetManager REST API."""

    @staticmethod
    def is_available() -> bool:
        """Check if OffsetManager is running."""
        try:
            r = requests.get(f"{OM_BASE_URL}/api/configs", timeout=_TIMEOUT)
            return r.ok
        except Exception:
            return False

    @staticmethod
    def list_configs() -> list[dict]:
        """List all vessel configurations.

        Returns list of dicts with: id, vessel_name, project_name, config_date.
        """
        try:
            r = requests.get(f"{OM_BASE_URL}/api/configs", timeout=_TIMEOUT)
            if r.ok:
                data = r.json()
                # API may return list directly or nested in 'configs' key
                if isinstance(data, list):
                    return data
                return data.get("configs", [])
        except Exception:
            pass
        return []

    @staticmethod
    def get_config(config_id: int) -> dict | None:
        """Get a single vessel configuration with its sensors."""
        try:
            r = requests.get(f"{OM_BASE_URL}/api/config/{config_id}", timeout=_TIMEOUT)
            if r.ok:
                return r.json()
        except Exception:
            pass
        return None

    @staticmethod
    def get_offsets(config_id: int) -> dict[str, dict]:
        """Get sensor offsets for a config as {sensor_name: {fields...}}.

        Returns dict keyed by sensor_name with x/y/z/roll/pitch/heading offsets.
        """
        try:
            r = requests.get(f"{OM_BASE_URL}/api/config/{config_id}", timeout=_TIMEOUT)
            if r.ok:
                data = r.json()
                sensors = data.get("sensors", data.get("offsets", []))
                result = {}
                for s in sensors:
                    name = s.get("sensor_name", s.get("name", ""))
                    result[name] = {
                        "sensor_type": s.get("sensor_type", ""),
                        "x": float(s.get("x_offset", 0)),
                        "y": float(s.get("y_offset", 0)),
                        "z": float(s.get("z_offset", 0)),
                        "roll": float(s.get("roll_offset", 0)),
                        "pitch": float(s.get("pitch_offset", 0)),
                        "heading": float(s.get("heading_offset", 0)),
                        "latency": float(s.get("latency", 0)),
                    }
                return result
        except Exception:
            pass
        return {}

"""OffsetManager REST API Client (default port 5302).

Provides read-only access to OffsetManager vessel configs and sensor offsets.
The runtime base URL can be overridden once at startup or via env vars:
``MBESQC_OM_BASE_URL`` and ``MBESQC_OM_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlparse

import requests

_DEFAULT_BASE_URL = "http://localhost:5302"
_DEFAULT_TIMEOUT_SECONDS = 2.0
_configured_base_url: str | None = None
_configured_timeout_seconds: float | None = None


@dataclass(frozen=True)
class OMRuntimeConfig:
    """Resolved startup settings for the OffsetManager boundary."""

    base_url: str
    timeout_seconds: float
    base_url_source: str
    timeout_source: str
    api_first: bool = True
    fallback_policy: str = "explicit-only"


def _normalize_base_url(value: str | None) -> str:
    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        return _DEFAULT_BASE_URL

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _DEFAULT_BASE_URL
    return candidate


def _normalize_timeout_seconds(value) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS
    return timeout if timeout > 0 else _DEFAULT_TIMEOUT_SECONDS


def _resolve_base_url() -> str:
    if _configured_base_url is not None:
        return _configured_base_url
    return _normalize_base_url(os.environ.get("MBESQC_OM_BASE_URL"))


def _resolve_timeout_seconds() -> float:
    if _configured_timeout_seconds is not None:
        return _configured_timeout_seconds
    return _normalize_timeout_seconds(os.environ.get("MBESQC_OM_TIMEOUT_SECONDS"))


def _request(path: str):
    return requests.get(f"{_resolve_base_url()}{path}", timeout=_resolve_timeout_seconds())


def resolve_runtime_config(
    *,
    om_base_url: str | None = None,
    om_timeout_seconds: float | int | None = None,
) -> OMRuntimeConfig:
    """Resolve the effective OM startup config without mutating global state."""
    if om_base_url is None:
        env_base_url = os.environ.get("MBESQC_OM_BASE_URL")
        om_base_url = env_base_url
        base_url_source = "env" if env_base_url else "default"
    else:
        base_url_source = "cli"

    if om_timeout_seconds is None:
        env_timeout_seconds = os.environ.get("MBESQC_OM_TIMEOUT_SECONDS")
        om_timeout_seconds = env_timeout_seconds
        timeout_source = "env" if env_timeout_seconds else "default"
    else:
        timeout_source = "cli"

    return OMRuntimeConfig(
        base_url=_normalize_base_url(om_base_url),
        timeout_seconds=_normalize_timeout_seconds(om_timeout_seconds),
        base_url_source=base_url_source,
        timeout_source=timeout_source,
    )


def build_runtime_report(
    *,
    om_base_url: str | None = None,
    om_timeout_seconds: float | int | None = None,
) -> dict:
    """Build an operator-facing summary of the effective OM runtime settings."""
    resolved = resolve_runtime_config(
        om_base_url=om_base_url,
        om_timeout_seconds=om_timeout_seconds,
    )
    reachable = False
    try:
        r = requests.get(
            f"{resolved.base_url}/api/configs",
            timeout=resolved.timeout_seconds,
        )
        reachable = r.ok
    except Exception:
        reachable = False

    return {
        "boundary": "api-first",
        "fallback_policy": resolved.fallback_policy,
        "base_url": resolved.base_url,
        "base_url_source": resolved.base_url_source,
        "timeout_seconds": resolved.timeout_seconds,
        "timeout_source": resolved.timeout_source,
        "api_reachable": reachable,
    }


def format_runtime_report(report: dict) -> str:
    """Format a runtime report for console output."""
    reachability = "reachable" if report.get("api_reachable") else "unreachable"
    return "\n".join(
        [
            "MBESQC OffsetManager self-check",
            f"  boundary: {report.get('boundary', 'api-first')} / {report.get('fallback_policy', 'explicit-only')}",
            f"  effective base URL: {report.get('base_url', _DEFAULT_BASE_URL)} ({report.get('base_url_source', 'default')})",
            f"  effective timeout: {report.get('timeout_seconds', _DEFAULT_TIMEOUT_SECONDS)}s ({report.get('timeout_source', 'default')})",
            f"  /api/configs probe: {reachability}",
            "  note: the desktop path stays API-first; any SQLite fallback must be explicit and confirmed elsewhere.",
        ]
    )


class OMClient:
    """Lightweight HTTP client for OffsetManager REST API."""

    @classmethod
    def configure(
        cls,
        *,
        base_url: str | None = None,
        timeout: float | int | None = None,
    ) -> None:
        """Pin the runtime OffsetManager connection settings.

        Passing ``None`` clears the explicit override and returns the client to
        env-driven resolution for the next call.
        """
        global _configured_base_url, _configured_timeout_seconds
        _configured_base_url = None if base_url is None else _normalize_base_url(base_url)
        _configured_timeout_seconds = None if timeout is None else _normalize_timeout_seconds(timeout)

    @staticmethod
    def is_available() -> bool:
        """Check if OffsetManager is running."""
        try:
            r = _request("/api/configs")
            return r.ok
        except Exception:
            return False

    @staticmethod
    def list_configs() -> list[dict]:
        """List all vessel configurations.

        Returns list of dicts with: id, vessel_name, project_name, config_date.
        """
        try:
            r = _request("/api/configs")
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
            r = _request(f"/api/config/{config_id}")
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
            r = _request(f"/api/config/{config_id}")
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

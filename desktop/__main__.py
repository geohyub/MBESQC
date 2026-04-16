"""Desktop launcher for MBESQC."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from desktop.main import main as launch_app
from desktop.services.om_client import build_runtime_report, format_runtime_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mbesqc", add_help=True)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print the effective OM runtime config and exit.",
    )
    parser.add_argument(
        "--self-check-report",
        default=None,
        type=Path,
        metavar="PATH",
        help="Write a bounded JSON proof packet for the self-check and exit.",
    )
    parser.add_argument(
        "--om-base-url",
        default=None,
        help="Override the OffsetManager API base URL for this process.",
    )
    parser.add_argument(
        "--om-timeout-seconds",
        default=None,
        help="Override the OffsetManager request timeout for this process.",
    )
    return parser


def _write_self_check_report(report: dict, report_path: Path) -> None:
    payload = {
        "type": "mbesqc.self-check-report",
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "boundary": report.get("boundary", "api-first"),
        "fallback_policy": report.get("fallback_policy", "explicit-only"),
        "base_url": report.get("base_url", ""),
        "base_url_source": report.get("base_url_source", "default"),
        "timeout_seconds": report.get("timeout_seconds", 2.0),
        "timeout_source": report.get("timeout_source", "default"),
        "api_reachable": bool(report.get("api_reachable", False)),
        "probe": "/api/configs",
        "note": "API-first runtime settings only; no OffsetManager data integrity or project DB proof.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.self_check:
        report = build_runtime_report(
            om_base_url=args.om_base_url,
            om_timeout_seconds=args.om_timeout_seconds,
        )
        print(format_runtime_report(report))
        if args.self_check_report:
            _write_self_check_report(report, args.self_check_report)
        return 0

    launch_app(
        om_base_url=args.om_base_url,
        om_timeout_seconds=args.om_timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

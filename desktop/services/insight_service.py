"""MBESQC insight helpers.

Centralizes project-context messaging, QC judgement summaries, and
screen/export narrative text so the UI and exported reports stay aligned.
"""

from __future__ import annotations

import json
import math
from datetime import datetime


QC_ORDER = [
    "preprocess",
    "file",
    "vessel",
    "offset",
    "motion",
    "svp",
    "coverage",
    "crossline",
    "surface",
]


QC_META = {
    "preprocess": {
        "label": "Pre-Processing",
        "weight": 10,
        "importance": "전처리 검증은 처리 시작 전에 이미 알려진 설정/항법 문제를 잡아 주는 첫 번째 방어선입니다.",
        "inputs": "PDS header / navigation / preprocessing metadata",
        "default_next": "전처리 FAIL/WARNING 항목을 먼저 정리한 뒤 본 QC 결과를 해석하세요.",
    },
    "file": {
        "label": "File QC",
        "weight": 5,
        "importance": "입력 파일 무결성과 시간 연속성이 흔들리면 뒤쪽 QC 해석도 같이 흔들립니다.",
        "inputs": "PDS / 등록 파일 목록",
        "default_next": "파일 무결성, 시간 연속성, 네이밍 규칙부터 다시 확인하세요.",
    },
    "vessel": {
        "label": "Vessel QC",
        "weight": 10,
        "importance": "선박/센서 기준이 어긋나면 이후 offset, motion, depth 해석이 모두 왜곡될 수 있습니다.",
        "inputs": "PDS + HVF",
        "default_next": "PDS 설정과 HVF 기준 오프셋 차이를 먼저 확인하세요.",
    },
    "offset": {
        "label": "Offset QC",
        "weight": 15,
        "importance": "오프셋 편차는 빔 패턴과 수심 품질에 직접 연결되므로 작은 차이도 반복되면 크게 보입니다.",
        "inputs": "GSF + PDS + HVF + OffsetManager",
        "default_next": "bias와 기준 오프셋 불일치를 같이 보고, 필요한 경우 patch test 또는 기준 설정을 재검토하세요.",
    },
    "motion": {
        "label": "Motion QC",
        "weight": 15,
        "importance": "자세 센서의 스파이크나 갭은 striping, depth noise, line mismatch로 이어질 수 있습니다.",
        "inputs": "GSF attitude samples",
        "default_next": "스파이크 축과 gap 구간을 먼저 찾아 원시 motion 품질을 확인하세요.",
    },
    "svp": {
        "label": "SVP QC",
        "weight": 10,
        "importance": "음속 프로파일 적용 상태가 맞지 않으면 전 구간 depth bias로 번질 수 있습니다.",
        "inputs": "GSF + SVP metadata",
        "default_next": "프로파일 적용 여부와 velocity range가 survey 조건에 맞는지 확인하세요.",
    },
    "coverage": {
        "label": "Coverage QC",
        "weight": 15,
        "importance": "커버리지는 데이터가 충분히 채워졌는지, 겹침이 부족하지 않은지 판단하는 기본 축입니다.",
        "inputs": "GSF tracklines",
        "default_next": "라인별 길이, swath 폭, overlap을 함께 보고 누락 구간이 있는지 확인하세요.",
    },
    "crossline": {
        "label": "Cross-line QC",
        "weight": 20,
        "importance": "Cross-line은 실제 수심 일치도를 보여주는 핵심 검증 축이라 전체 판정에서 가장 무겁습니다.",
        "inputs": "GSF multi-line intersections",
        "default_next": "IHO pass rate와 교차점 분산을 먼저 보고, striping 여부를 같이 판단하세요.",
    },
    "surface": {
        "label": "Surface",
        "weight": 10,
        "importance": "Surface는 DTM/density/std 생성 가능 여부를 보여줘 후속 deliverable 준비 상태를 판단하게 해줍니다.",
        "inputs": "GSF point cloud",
        "default_next": "표면이 비어 있으면 입력 point coverage와 gridding 조건을 먼저 확인하세요.",
    },
}


_STATUS_RANK = {
    "FAIL": 0,
    "WARNING": 1,
    "PASS": 2,
    "INFO": 3,
    "N/A": 4,
    "---": 5,
}


def normalize_status(status: str | None) -> str:
    text = str(status or "N/A").strip().upper()
    return text if text in _STATUS_RANK else "N/A"


def format_number(value, decimals: int = 1, unit: str = "", empty: str = "---") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return empty
    if math.isnan(number) or math.isinf(number):
        return empty
    text = f"{number:,.{decimals}f}"
    return f"{text}{unit}" if unit else text


def format_datetime(value: str | None, empty: str = "---") -> str:
    if not value:
        return empty
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return value[:16] if len(value) >= 16 else value


def bullet_text(lines: list[str]) -> str:
    clean = [line.strip() for line in lines if line and line.strip()]
    return "\n".join(f"• {line}" for line in clean)


def describe_result_snapshot(result: dict | None, anchor_file: dict | None = None) -> str:
    """Human-readable label for a stored project QC snapshot."""
    label = "Project QC snapshot"
    filename = (anchor_file or {}).get("filename", "")
    if filename:
        return f"{label} | entry file {filename}"
    return label


def summarize_text(
    text: str | None,
    max_chars: int = 160,
    max_lines: int = 2,
    empty: str = "---",
) -> str:
    """Compact multi-line evidence text for UI/export summaries."""
    value = str(text or "").replace("\r", "")
    for old, new in (("°", " deg"), ("±", "+/-"), ("→", "->"), ("—", "-"), ("–", "-")):
        value = value.replace(old, new)
    lines = [" ".join(line.strip().split()) for line in value.split("\n") if line.strip()]
    if not lines:
        return empty
    extra = max(0, len(lines) - max_lines)
    lines = lines[:max_lines]
    compact = " / ".join(lines)
    if extra:
        compact += f" / ... {extra} more"
    if len(compact) > max_chars:
        compact = compact[: max_chars - 3].rstrip() + "..."
    return compact


def get_section_status(qc_id: str, result_data: dict) -> str:
    section = result_data.get(qc_id, {}) or {}
    status = normalize_status(section.get("overall") or section.get("verdict"))
    if qc_id == "offset":
        ov = result_data.get("offset_validation", {}) or {}
        status = worse_status(status, normalize_status(ov.get("overall")))
    return status


def worse_status(*statuses: str) -> str:
    candidates = [normalize_status(s) for s in statuses if s is not None]
    if not candidates:
        return "N/A"
    return min(candidates, key=lambda s: _STATUS_RANK.get(s, 99))


def get_status_counts(result_data: dict) -> dict[str, int]:
    counts = {"FAIL": 0, "WARNING": 0, "PASS": 0, "INFO": 0, "N/A": 0}
    for qc_id in QC_ORDER:
        section = result_data.get(qc_id, {}) or {}
        if not section:
            counts["N/A"] += 1
            continue
        status = get_section_status(qc_id, result_data)
        counts[status] = counts.get(status, 0) + 1
    return counts


def pick_focus_module(result_data: dict) -> str | None:
    candidates: list[tuple[tuple[int, int], str]] = []
    for qc_id in QC_ORDER:
        section = result_data.get(qc_id, {}) or {}
        if not section:
            continue
        status = get_section_status(qc_id, result_data)
        weight = QC_META.get(qc_id, {}).get("weight", 0)
        candidates.append(((_STATUS_RANK.get(status, 99), -weight), qc_id))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def build_result_overview(result_data: dict) -> dict:
    if not result_data:
        return {
            "headline": "아직 QC 결과가 없습니다.",
            "body": "프로젝트 QC를 한 번 실행하면 전체 판단 요약과 우선 확인 모듈이 여기에 정리됩니다.",
            "next_steps": ["프로젝트 QC를 실행해 최신 스냅샷을 만든 뒤 결과를 검토하세요."],
            "focus_qc": None,
            "critical_findings": [],
            "action_items": ["프로젝트 QC를 실행해 최신 스냅샷을 만든 뒤 결과를 검토하세요."],
        }

    counts = get_status_counts(result_data)
    focus_qc = pick_focus_module(result_data)
    focus_label = QC_META.get(focus_qc or "", {}).get("label", "QC 모듈")

    if counts["FAIL"] > 0:
        headline = f"{focus_label}이 현재 가장 중요한 확인 포인트입니다."
    elif counts["WARNING"] > 0:
        headline = f"{focus_label}부터 보면 현재 위험 신호를 가장 빨리 이해할 수 있습니다."
    else:
        headline = "현재 결과는 전반적으로 정상 범위에 가깝습니다."

    body_parts = [
        f"{len(QC_ORDER)}개 모듈 중 FAIL {counts['FAIL']}개, WARNING {counts['WARNING']}개, PASS {counts['PASS']}개입니다.",
    ]
    preprocess_status = get_section_status("preprocess", result_data)
    if preprocess_status in ("FAIL", "WARNING"):
        preprocess_story = build_module_story("preprocess", result_data)
        body_parts.append("전처리 기준도 함께 확인해야 합니다. " + preprocess_story["current_reading"])
    if focus_qc:
        story = build_module_story(focus_qc, result_data)
        body_parts.append(story["headline"])
        next_steps = story["next_steps"][:3]
    else:
        next_steps = ["모듈 카드에서 우선순위가 높은 항목부터 세부 근거를 확인하세요."]

    critical_findings = build_issue_spotlight(result_data)
    action_items = build_action_checklist(result_data)
    if critical_findings:
        body_parts.append(f"핵심 이슈 {len(critical_findings)}개를 별도 spotlight로 정리했습니다.")

    return {
        "headline": headline,
        "body": " ".join(body_parts),
        "next_steps": next_steps,
        "focus_qc": focus_qc,
        "critical_findings": critical_findings,
        "action_items": action_items,
    }


def build_issue_spotlight(result_data: dict, limit: int = 5) -> list[dict]:
    """Top cross-module findings that deserve operator attention first."""
    findings: list[dict] = []
    for qc_id in QC_ORDER:
        section = result_data.get(qc_id, {}) or {}
        if not section and qc_id != "offset":
            continue

        story = build_module_story(qc_id, result_data)
        module_meta = QC_META.get(qc_id, {})
        module_label = module_meta.get("label", qc_id)
        module_weight = module_meta.get("weight", 0)
        module_status = story["status"]
        if module_status not in ("FAIL", "WARNING"):
            continue

        issues = _collect_problem_items(qc_id, result_data)
        if issues:
            issue_limit = 2 if module_status == "FAIL" else 1
            for order, issue in enumerate(issues[:issue_limit]):
                findings.append({
                    "qc_id": qc_id,
                    "module": module_label,
                    "status": issue["status"],
                    "title": issue["name"],
                    "focus_name": issue["name"],
                    "evidence": summarize_text(issue.get("detail")),
                    "action": story["next_steps"][0] if story.get("next_steps") else "",
                    "importance": story["importance"],
                    "_sort": (_STATUS_RANK.get(issue["status"], 99), -module_weight, order),
                })
        else:
            findings.append({
                "qc_id": qc_id,
                "module": module_label,
                "status": module_status,
                "title": story["headline"],
                "focus_name": "",
                "evidence": summarize_text(story["current_reading"]),
                "action": story["next_steps"][0] if story.get("next_steps") else "",
                "importance": story["importance"],
                "_sort": (_STATUS_RANK.get(module_status, 99), -module_weight, 0),
            })

    findings.sort(key=lambda item: item["_sort"])
    trimmed = findings[:limit]
    for item in trimmed:
        item.pop("_sort", None)
    return trimmed


def build_action_checklist(result_data: dict, limit: int = 4) -> list[str]:
    """Distinct next actions distilled from the most important findings."""
    actions: list[str] = []
    seen: set[str] = set()
    findings = build_issue_spotlight(result_data, limit=max(limit * 2, 6))
    for finding in findings:
        action = (finding.get("action") or "").strip()
        if not action:
            continue
        text = f"{finding['module']}: {action}"
        if text not in seen:
            actions.append(text)
            seen.add(text)
        if len(actions) >= limit:
            break

    if not actions and result_data:
        focus_qc = pick_focus_module(result_data)
        if focus_qc:
            focus_story = build_module_story(focus_qc, result_data)
            for step in focus_story.get("next_steps", []):
                if step not in seen:
                    actions.append(step)
                    seen.add(step)
                if len(actions) >= limit:
                    break

    if not actions and result_data:
        actions.append("현재 QC 결과는 정상 범위에 가깝습니다. export 전에 최신 스냅샷만 다시 확인하세요.")
    return actions[:limit]


def build_history_story(result_rows: list[dict], limit: int = 5) -> dict:
    """Explain recent QC trend and recurring issues across project snapshots."""
    done_results = [
        row for row in (result_rows or [])
        if str(row.get("status", "")).lower() == "done"
    ]
    done_results.sort(
        key=lambda row: (
            row.get("finished_at") or row.get("started_at") or "",
            row.get("id") or 0,
        ),
        reverse=True,
    )

    if not done_results:
        return {
            "headline": "최근 완료된 QC 스냅샷이 아직 없습니다.",
            "body": "프로젝트 QC를 1회 이상 완료하면 점수 추세와 반복 이슈를 같이 읽을 수 있습니다.",
            "trend_status": "none",
            "score_delta": None,
            "persistent_modules": [],
            "worsened_modules": [],
            "improved_modules": [],
            "recent_scores": [],
        }

    recent = done_results[:limit]
    recent_payloads = [_load_result_payload(row) for row in recent]
    recent_scores = [_score_value(row.get("score")) for row in recent]
    valid_scores = [score for score in recent_scores if score is not None]

    latest = recent[0]
    latest_score = _score_value(latest.get("score"))
    previous = recent[1] if len(recent) > 1 else None
    previous_score = _score_value(previous.get("score")) if previous else None

    if latest_score is None:
        headline = "최근 QC 스냅샷 점수를 읽지 못했습니다."
        trend_status = "unknown"
        score_delta = None
    elif previous_score is None:
        headline = (
            f"최근 QC 히스토리는 1회이며 최신 스냅샷은 "
            f"{format_number(latest_score, 1)}점입니다."
        )
        trend_status = "single"
        score_delta = None
    else:
        score_delta = latest_score - previous_score
        if score_delta >= 1.0:
            trend_status = "improving"
            headline = (
                f"최신 스냅샷은 {format_number(latest_score, 1)}점으로 "
                f"직전 대비 {format_number(score_delta, 1)}점 개선되었습니다."
            )
        elif score_delta <= -1.0:
            trend_status = "worsening"
            headline = (
                f"최신 스냅샷은 {format_number(latest_score, 1)}점으로 "
                f"직전 대비 {format_number(abs(score_delta), 1)}점 악화되었습니다."
            )
        else:
            trend_status = "stable"
            headline = (
                f"최신 스냅샷은 {format_number(latest_score, 1)}점으로 "
                "직전과 큰 차이 없이 유지되고 있습니다."
            )

    latest_payload = recent_payloads[0] if recent_payloads else {}
    problem_counts: dict[str, int] = {}
    latest_problem_modules: set[str] = set()
    for idx, payload in enumerate(recent_payloads):
        for qc_id in QC_ORDER:
            status = get_section_status(qc_id, payload)
            if status in ("FAIL", "WARNING"):
                problem_counts[qc_id] = problem_counts.get(qc_id, 0) + 1
                if idx == 0:
                    latest_problem_modules.add(qc_id)

    persistent_modules = [
        QC_META.get(qc_id, {}).get("label", qc_id)
        for qc_id in QC_ORDER
        if qc_id in latest_problem_modules and problem_counts.get(qc_id, 0) >= 2
    ]

    worsened_modules: list[str] = []
    improved_modules: list[str] = []
    if len(recent_payloads) >= 2:
        previous_payload = recent_payloads[1]
        for qc_id in QC_ORDER:
            latest_status = get_section_status(qc_id, latest_payload)
            previous_status = get_section_status(qc_id, previous_payload)
            if latest_status == "N/A" or previous_status == "N/A":
                continue
            latest_rank = _STATUS_RANK.get(latest_status, 99)
            previous_rank = _STATUS_RANK.get(previous_status, 99)
            label = QC_META.get(qc_id, {}).get("label", qc_id)
            if latest_rank < previous_rank:
                worsened_modules.append(label)
            elif latest_rank > previous_rank:
                improved_modules.append(label)

    body_parts = []
    if valid_scores:
        body_parts.append(
            f"최근 {len(valid_scores)}회 평균 점수는 {format_number(sum(valid_scores) / len(valid_scores), 1)}점입니다."
        )
    if persistent_modules:
        body_parts.append("반복 이슈는 " + ", ".join(persistent_modules[:4]) + "입니다.")
    if worsened_modules:
        body_parts.append("직전 대비 악화된 모듈은 " + ", ".join(worsened_modules[:4]) + "입니다.")
    if improved_modules:
        body_parts.append("직전 대비 개선된 모듈은 " + ", ".join(improved_modules[:4]) + "입니다.")
    if not body_parts:
        body_parts.append("최근 스냅샷 간 큰 추세 변화나 반복 이슈는 아직 두드러지지 않습니다.")

    return {
        "headline": headline,
        "body": " ".join(body_parts),
        "trend_status": trend_status,
        "score_delta": score_delta,
        "persistent_modules": persistent_modules,
        "worsened_modules": worsened_modules,
        "improved_modules": improved_modules,
        "recent_scores": valid_scores,
    }


def build_run_diff(result_rows: list[dict], limit: int = 5) -> dict:
    """Summarize module-level differences between the latest two completed runs."""
    done_results = [
        row for row in (result_rows or [])
        if str(row.get("status", "")).lower() == "done"
    ]
    done_results.sort(
        key=lambda row: (
            row.get("finished_at") or row.get("started_at") or "",
            row.get("id") or 0,
        ),
        reverse=True,
    )

    if len(done_results) < 2:
        return {
            "headline": "직전 비교용 완료 스냅샷이 아직 부족합니다.",
            "body": "최소 2회 이상 프로젝트 QC를 완료하면 모듈별 변화 근거를 비교할 수 있습니다.",
            "changes": [],
        }

    latest = done_results[0]
    previous = done_results[1]
    latest_data = _load_result_payload(latest)
    previous_data = _load_result_payload(previous)

    changes: list[dict] = []
    for qc_id in QC_ORDER:
        latest_status = get_section_status(qc_id, latest_data)
        previous_status = get_section_status(qc_id, previous_data)
        if latest_status == "N/A" and previous_status == "N/A":
            continue
        if latest_status == previous_status:
            continue

        latest_story = build_module_story(qc_id, latest_data)
        previous_story = build_module_story(qc_id, previous_data)
        changes.append({
            "qc_id": qc_id,
            "module": QC_META.get(qc_id, {}).get("label", qc_id),
            "previous_status": previous_status,
            "latest_status": latest_status,
            "direction": _status_direction(previous_status, latest_status),
            "previous_reading": summarize_text(previous_story.get("current_reading")),
            "latest_reading": summarize_text(latest_story.get("current_reading")),
            "_sort": (_STATUS_RANK.get(latest_status, 99), _STATUS_RANK.get(previous_status, 99)),
        })

    changes.sort(key=lambda item: item["_sort"])
    trimmed = changes[:limit]
    for item in trimmed:
        item.pop("_sort", None)

    if not trimmed:
        return {
            "headline": "직전 완료 스냅샷과 비교해 모듈 판정 변화는 크지 않습니다.",
            "body": "세부 수치 변화는 있을 수 있지만 PASS/WARNING/FAIL 기준으로는 동일하게 유지됐습니다.",
            "changes": [],
        }

    summary_parts = [
        f"{item['module']} {item['previous_status']} -> {item['latest_status']}"
        for item in trimmed[:3]
    ]
    return {
        "headline": "직전 완료 스냅샷 대비 바뀐 모듈이 있습니다.",
        "body": " / ".join(summary_parts),
        "changes": trimmed,
    }


def build_settings_assistant(
    project: dict | None,
    result_data: dict | None = None,
    file_counts: dict | None = None,
) -> dict:
    """Translate current project settings into operator-facing guidance."""
    if not project:
        return {
            "headline": "프로젝트 설정 정보를 아직 읽지 못했습니다.",
            "current_state": [],
            "recommendations": [],
        }

    file_counts = file_counts or {}
    gsf_count = int(file_counts.get("gsf_count") or 0)
    pds_count = int(file_counts.get("pds_count") or 0)
    max_pings = int(project.get("max_pings") or 0)
    max_gsf = int(project.get("max_gsf_files") or 0)
    cell_size = float(project.get("cell_size") or 0.0)

    current_state = []
    recommendations = []

    if max_pings > 0:
        current_state.append(f"Max pings {max_pings:,}: preview/sampled run")
        recommendations.append("최종 판정 전에 Max pings를 0으로 돌려 전체 핑 기준 결과를 한 번 더 확인하세요.")
    else:
        current_state.append("Max pings 0: full ping scope")

    if max_gsf > 0 and gsf_count and max_gsf < gsf_count:
        current_state.append(f"Max GSF Files {max_gsf:,}: {gsf_count:,}개 중 일부 라인만 사용")
        recommendations.append("Coverage/Cross-line 판단이 중요하면 Max GSF Files를 전체로 늘려 재실행하세요.")
    else:
        current_state.append(
            "Max GSF Files 전체"
            if not max_gsf
            else f"Max GSF Files {max_gsf:,}: 현재 입력 범위 대부분 포함"
        )

    if cell_size > 0:
        current_state.append(f"Cell size {format_number(cell_size, 1, 'm')}")
        if cell_size >= 8.0:
            recommendations.append("Cell size가 큰 편입니다. Surface/Coverage 세부 확인이 필요하면 3-5m 재실행도 검토하세요.")

    if pds_count == 0:
        recommendations.append("PDS가 없으면 vessel/offset 설명력이 약해집니다. 가능하면 PDS를 연결하세요.")
    if not project.get("hvf_dir"):
        recommendations.append("HVF가 없으면 vessel 기준 비교가 약해집니다. 기준 HVF를 연결하는 편이 안전합니다.")
    if not project.get("om_config_id") and get_section_status("offset", result_data or {}) in ("FAIL", "WARNING"):
        recommendations.append("Offset 이슈가 있는데 OffsetManager 기준이 없습니다. OM 연결 후 cross-check를 권장합니다.")

    if not recommendations:
        recommendations.append("현재 설정은 해석 가능한 범위입니다. 설정을 바꿨다면 같은 프로젝트로 다시 QC를 실행해 추세를 비교하세요.")

    return {
        "headline": "현재 QC 해석에 영향을 주는 설정",
        "current_state": current_state,
        "recommendations": recommendations[:4],
    }


def build_module_rows(result_data: dict) -> list[dict]:
    rows = []
    for qc_id in QC_ORDER:
        section = result_data.get(qc_id, {}) or {}
        if not section and qc_id != "offset":
            continue
        story = build_module_story(qc_id, result_data)
        if story["status"] == "N/A" and not section:
            continue
        rows.append(story)
    return rows


def build_module_story(qc_id: str, result_data: dict) -> dict:
    section = result_data.get(qc_id, {}) or {}
    meta = QC_META.get(qc_id, {"label": qc_id, "importance": "", "inputs": "", "default_next": ""})
    status = get_section_status(qc_id, result_data)

    if not section and qc_id != "offset":
        return {
            "module": meta["label"],
            "status": "N/A",
            "headline": f"{meta['label']} 결과가 아직 없습니다.",
            "importance": meta["importance"],
            "current_reading": "이 모듈에 사용할 데이터가 아직 준비되지 않았습니다.",
            "next_steps": [meta["default_next"]],
            "source_text": f"입력 기준: {meta['inputs']}",
        }

    current_reading = ""
    next_steps: list[str] = []

    if qc_id == "preprocess":
        current_reading = (
            f"전처리 기준에서 FAIL {format_number(section.get('num_fail'), 0)}개, "
            f"WARNING {format_number(section.get('num_warn'), 0)}개가 확인됐습니다."
        )
        failing = [
            item.get("name", "")
            for item in section.get("items", [])
            if normalize_status(item.get("status")) == "FAIL"
        ]
        if failing:
            current_reading += f" 현재 치명 항목: {', '.join(failing[:2])}."
        next_steps = [meta["default_next"]]

    elif qc_id == "file":
        issues = _problem_items(section.get("items", []))
        if issues:
            current_reading = f"문제가 된 근거는 {_join_issue_names(issues)}입니다."
        else:
            current_reading = "등록된 입력 파일 무결성, 네이밍, 시간 연속성에서 큰 이상이 보이지 않습니다."
        next_steps = [meta["default_next"]]

    elif qc_id == "vessel":
        issues = _problem_items(section.get("items", []))
        if issues:
            current_reading = f"PDS와 HVF 기준 비교에서 {_join_issue_names(issues)} 차이가 확인되었습니다."
        else:
            current_reading = "PDS 설정과 HVF 기준 오프셋 비교에서 큰 불일치가 보이지 않습니다."
        next_steps = [meta["default_next"]]

    elif qc_id == "offset":
        roll = format_number(section.get("roll_bias_deg"), 4, "°")
        pitch = format_number(section.get("pitch_bias_deg"), 4, "°")
        ov = result_data.get("offset_validation", {}) or {}
        config_checks = _problem_items(ov.get("config_checks", []), name_key="sensor")
        data_checks = _problem_items(ov.get("data_checks", []))
        if config_checks or data_checks or status in ("FAIL", "WARNING"):
            pieces = [f"Roll bias {roll}", f"Pitch bias {pitch}"]
            if config_checks:
                pieces.append(f"OffsetManager 비교 이슈 {_join_issue_names(config_checks, name_key='sensor')}")
            if data_checks:
                pieces.append(f"데이터 체크 이슈 {_join_issue_names(data_checks)}")
            current_reading = ", ".join(pieces) + "가 현재 판단의 근거입니다."
        else:
            current_reading = f"Roll bias {roll}, Pitch bias {pitch} 수준으로 큰 기준 이탈이 보이지 않습니다."
        next_steps = [meta["default_next"]]

    elif qc_id == "motion":
        axes = section.get("axes", {}) or {}
        axis_issues = []
        for axis_name, axis_data in axes.items():
            axis_status = normalize_status(axis_data.get("verdict"))
            if axis_status in ("FAIL", "WARNING"):
                axis_issues.append(f"{axis_data.get('name', axis_name)} {axis_status}")
        gap_status = normalize_status(section.get("gap_verdict"))
        if axis_issues or gap_status in ("FAIL", "WARNING"):
            parts = []
            if axis_issues:
                parts.append("축 이슈 " + ", ".join(axis_issues))
            if gap_status in ("FAIL", "WARNING"):
                parts.append(
                    f"gap {gap_status} (최대 {format_number(section.get('max_gap_sec'), 3, 's')})"
                )
            current_reading = " / ".join(parts) + "가 현재 motion 판단의 근거입니다."
        else:
            current_reading = (
                f"샘플 {format_number(section.get('total_samples'), 0)}개, "
                f"최대 gap {format_number(section.get('max_gap_sec'), 3, 's')} 수준으로 안정적입니다."
            )
        next_steps = [meta["default_next"]]

    elif qc_id == "svp":
        applied = "적용됨" if section.get("applied") else "미적용"
        velocity_range = section.get("velocity_range", [0, 0]) or [0, 0]
        issues = _problem_items(section.get("items", []))
        if issues:
            current_reading = f"SVP {applied}, profiles {format_number(section.get('num_profiles'), 0)}개이며 {_join_issue_names(issues)}에서 경고가 나왔습니다."
        else:
            current_reading = (
                f"SVP {applied}, profiles {format_number(section.get('num_profiles'), 0)}개, "
                f"velocity {format_number(velocity_range[0], 1)} ~ {format_number(velocity_range[1], 1)} m/s 범위입니다."
            )
        next_steps = [meta["default_next"]]

    elif qc_id == "coverage":
        overlap = format_number(section.get("mean_overlap_pct"), 1, "%")
        total_lines = format_number(section.get("total_lines"), 0)
        total_length = format_number(section.get("total_length_km"), 1, " km")
        issues = _problem_items(section.get("items", []))
        if issues:
            current_reading = f"라인 {total_lines}개, 총 {total_length}, 평균 overlap {overlap}이며 {_join_issue_names(issues)}가 현재 이슈입니다."
        else:
            current_reading = f"라인 {total_lines}개, 총 {total_length}, 평균 overlap {overlap}로 커버리지가 안정적입니다."
        next_steps = [meta["default_next"]]

    elif qc_id == "crossline":
        pass_pct = format_number(section.get("iho_pass_pct"), 1, "%")
        rms = format_number(section.get("depth_diff_rms"), 4, " m")
        mean = format_number(section.get("depth_diff_mean"), 4, " m")
        striping = "감지됨" if section.get("striping_detected") else "감지되지 않음"
        current_reading = (
            f"IHO pass {pass_pct}, mean |dZ| {mean}, RMS {rms}, striping {striping}가 현재 핵심 근거입니다."
        )
        next_steps = [meta["default_next"]]

    elif qc_id == "surface":
        if section.get("has_dtm"):
            current_reading = (
                f"Surface 생성 가능 상태입니다. points {format_number(section.get('num_points'), 0)}, "
                f"grid {format_number(section.get('nx'), 0)} x {format_number(section.get('ny'), 0)}, "
                f"cell {format_number(section.get('cell_size'), 1, ' m')}."
            )
        else:
            current_reading = "Surface 생성에 필요한 point coverage 또는 gridding 입력이 부족합니다."
        next_steps = [meta["default_next"]]

    if not current_reading:
        current_reading = "현재 판정 근거를 해석할 데이터가 충분하지 않습니다."
    if not next_steps:
        next_steps = [meta["default_next"]]

    if status == "PASS":
        headline = f"{meta['label']}은 현재 기준에서 정상 범위에 가깝습니다."
    elif status == "WARNING":
        headline = f"{meta['label']}은 지금 바로 다시 볼 가치가 있는 경고 상태입니다."
    elif status == "FAIL":
        headline = f"{meta['label']}은 현재 결과를 좌우하는 핵심 문제 구간입니다."
    else:
        headline = f"{meta['label']} 결과를 아직 확정하기 어렵습니다."

    return {
        "module": meta["label"],
        "status": status,
        "headline": headline,
        "importance": meta["importance"],
        "current_reading": current_reading,
        "next_steps": next_steps,
        "source_text": f"입력 기준: {meta['inputs']}",
    }


def build_project_context(
    project: dict | None,
    latest_result: dict | None = None,
    om_preview: dict | None = None,
    file_counts: dict | None = None,
) -> dict:
    if not project:
        return {
            "flow": "프로젝트 정보가 아직 없습니다.",
            "readiness_text": "",
            "offset_text": "OffsetManager 연결 정보가 없습니다.",
            "snapshot_text": "최근 QC 스냅샷이 없습니다.",
            "export_text": "Export는 최신 완료 QC 스냅샷을 기준으로 생성됩니다.",
        }

    file_counts = file_counts or {}
    pds_count = file_counts.get("pds_count")
    gsf_count = file_counts.get("gsf_count")
    hvf_count = file_counts.get("hvf_count")

    readiness = [
        _source_line(
            "PDS",
            bool(project.get("pds_dir")),
            f"{_count_text(pds_count, '개 파일')} | 선박 설정과 PDS 헤더 검증 기준",
        ),
        _source_line(
            "GSF",
            bool(project.get("gsf_dir")),
            f"{_count_text(gsf_count, '개 파일')} | sounding / motion / coverage 실제 데이터",
        ),
        _source_line(
            "HVF",
            bool(project.get("hvf_dir")),
            f"{_count_text(hvf_count, '개 파일')} | 센서 기준 오프셋 비교용 참조",
        ),
        _source_line(
            "OffsetManager",
            bool(project.get("om_config_id")),
            f"Config {project.get('om_config_id') or '미연결'} | 외부 기준 오프셋 교차 검증",
            optional=True,
        ),
    ]

    flow = (
        "프로젝트는 입력 폴더와 기준 설정을 묶는 QC 작업 공간입니다. "
        "분석 화면에 들어갈 때 고른 파일은 진입 기준일 뿐이고, QC와 export 해석은 "
        "최신 프로젝트 QC 스냅샷 중심으로 보는 편이 안전합니다."
    )

    offset_text = "OffsetManager 미연결: offset 검증은 PDS/HVF 중심으로만 해석됩니다."
    if project.get("om_config_id"):
        if om_preview and om_preview.get("config"):
            cfg = om_preview["config"]
            sensors = om_preview.get("sensors", [])
            snapshot = om_preview.get("snapshot", {}) or {}
            base_text = (
                f"OffsetManager 기준: {cfg.get('vessel_name', '')} / {cfg.get('project_name', '')} / "
                f"{cfg.get('config_date', '') or 'date 미기입'} / reference {cfg.get('reference_point', 'COG')} / "
                f"sensors {len(sensors):,}개. "
                "이 연결은 읽기 전용 비교 기준이며 MBESQC가 원본 오프셋을 수정하지는 않습니다."
            )
            if snapshot:
                role = snapshot.get("role", {}).get("label", "")
                readiness = snapshot.get("readiness", {}).get("label", "")
                review = snapshot.get("review", {}).get("label", "")
                baseline = snapshot.get("baseline_text", "")
                sensor_groups = snapshot.get("sensor_group_text", "")
                extra = []
                if review:
                    extra.append(f"Approval: {review}")
                if sensor_groups:
                    extra.append(sensor_groups)
                if role or readiness:
                    extra.append(f"OM 판단: {role or '역할 미확인'} / {readiness or '준비도 미확인'}")
                if baseline:
                    extra.append(baseline)
                offset_text = base_text + (" " + " ".join(extra) if extra else "")
            else:
                offset_text = base_text
        else:
            offset_text = (
                f"OffsetManager config {project.get('om_config_id')}가 연결되어 있지만 미리보기를 가져오지 못했습니다. "
                "MBESQC에서는 이를 읽기 전용 비교 기준으로만 사용합니다."
            )

    snapshot_text = "최근 완료 QC 스냅샷이 없습니다."
    if latest_result:
        focus_qc = None
        try:
            result_data = json.loads(latest_result.get("result_json", "{}"))
            focus_qc = pick_focus_module(result_data)
        except (TypeError, json.JSONDecodeError):
            result_data = {}
        focus_label = QC_META.get(focus_qc or "", {}).get("label")
        suffix = f" | 우선 확인: {focus_label}" if focus_label else ""
        snapshot_text = (
            f"최근 완료 QC: {format_number(latest_result.get('score'), 1)}점 / "
            f"{latest_result.get('grade', '') or '미지정'} / "
            f"{format_datetime(latest_result.get('finished_at'))}{suffix}"
        )

    export_text = "Export는 최신 완료 QC 스냅샷과 모듈 해설을 기준으로 생성됩니다."

    return {
        "flow": flow,
        "readiness_text": bullet_text(readiness),
        "offset_text": offset_text,
        "snapshot_text": snapshot_text,
        "export_text": export_text,
    }


def _count_text(count: int | None, suffix: str) -> str:
    if count is None:
        return "개수 미확인"
    return f"{count:,}{suffix}"


def _source_line(name: str, ready: bool, detail: str, optional: bool = False) -> str:
    if ready:
        status = "READY"
    elif optional:
        status = "OPTIONAL"
    else:
        status = "MISSING"
    return f"[{status}] {name}: {detail}"


def _problem_items(items, name_key: str = "name") -> list[dict]:
    problems = []
    for item in items or []:
        status = normalize_status(item.get("status"))
        if status in ("FAIL", "WARNING"):
            problems.append(item)
    return problems[:3]


def _normalize_issue_item(item: dict, fallback_name: str = "Issue") -> dict:
    name = (
        item.get("name")
        or " ".join(
            part for part in (item.get("sensor"), item.get("field"))
            if part
        ).strip()
        or fallback_name
    )
    detail_parts = []
    if item.get("detail"):
        detail_parts.append(str(item.get("detail")))
    elif item.get("pds_value") or item.get("om_value") or item.get("hvf_value") or item.get("reference_value") or item.get("difference"):
        if item.get("pds_value"):
            detail_parts.append(f"PDS={item.get('pds_value')}")
        if item.get("hvf_value"):
            detail_parts.append(f"HVF={item.get('hvf_value')}")
        if item.get("om_value"):
            detail_parts.append(f"OM={item.get('om_value')}")
        if item.get("reference_value"):
            detail_parts.append(f"Ref={item.get('reference_value')}")
        if item.get("difference"):
            detail_parts.append(f"Diff={item.get('difference')}")
    return {
        "name": name,
        "status": normalize_status(item.get("status")),
        "detail": " | ".join(part for part in detail_parts if part),
    }


def _collect_problem_items(qc_id: str, result_data: dict) -> list[dict]:
    section = result_data.get(qc_id, {}) or {}
    raw_items = list(section.get("items", []) or [])
    if qc_id == "offset":
        ov = result_data.get("offset_validation", {}) or {}
        raw_items.extend(ov.get("config_checks", []) or [])
        raw_items.extend(ov.get("data_checks", []) or [])

    items = [
        _normalize_issue_item(
            item,
            fallback_name=QC_META.get(qc_id, {}).get("label", qc_id),
        )
        for item in raw_items
    ]
    return [
        item for item in items
        if item["status"] in ("FAIL", "WARNING")
    ]


def _join_issue_names(items: list[dict], name_key: str = "name") -> str:
    names = []
    for item in items:
        name = item.get(name_key) or item.get("name") or item.get("field") or "항목"
        status = normalize_status(item.get("status"))
        names.append(f"{name} {status}")
    return ", ".join(names) if names else "주요 항목"


def _load_result_payload(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        return json.loads(row.get("result_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _score_value(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _status_direction(previous_status: str, latest_status: str) -> str:
    previous_rank = _STATUS_RANK.get(previous_status, 99)
    latest_rank = _STATUS_RANK.get(latest_status, 99)
    if latest_rank < previous_rank:
        return "worsened"
    if latest_rank > previous_rank:
        return "improved"
    return "changed"

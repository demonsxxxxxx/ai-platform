from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from runtime_guard import require_internal_context

SCHEMA_VERSION = "ctd-32s73-fact-packet-v1"
REQUIRED_TOP_LEVEL_KEYS = [
    "schema_version",
    "project_profile",
    "sources",
    "long_term",
    "accelerated",
    "stress_study",
    "trend_charts",
    "body_sections",
    "docx_render_plan",
    "missing_evidence",
    "manual_review_items",
    "agent_provenance",
]
STUDY_KEYS = ["long_term", "accelerated", "stress_study"]
REQUIRED_TABLE_KEYS = ["role", "batch_no", "time_header", "rows"]
VALID_BATCH_STRUCTURE_ACTIONS = {"keep", "shrink", "expand"}
STRESS_ROLES = {
    "stress",
    "light_stress",
    "agitation_stress",
    "freeze_thaw_stress",
    "high_temperature_stress",
    "low_ph_stress",
    "high_ph_stress",
    "oxidation_stress",
}
STABILITY_ROLES = {"long_term", "accelerated"}
TREND_STRESS_MARKERS = ["影响因素", "强制降解", "光照", "振荡", "震荡", "冻融", "高温", "低pH", "高pH", "低ph", "高ph", "氧化"]
MISSING_MARKERS = {"", "N/A", "NA", "n/a", "na", "---", None}
LIMIT_PREFIXES = ("<", ">", "<=", ">=", "≤", "≥", "＜", "＞")
TREND_EXCLUSION_MARKERS = ["trend", "chart", "figure", "趋势", "图", "不生成", "排除", "缺口", "无法", "不足"]
BODY_SECTION_PATTERN_REF_KEYS = {"writing_pattern_refs", "pattern_refs", "drafting_refs"}
BODY_SECTION_FORBIDDEN_TEXT_MARKERS = ["XXX", "IPXXX", "略微上升/略微下降/波动"]
DEFERRED_METADATA_KEYS = {"status", "deferred", "deferred_until", "reason", "notes", "comment"}
DEFERRED_STATUS_MARKERS = {
    "deferred",
    "deferred_until_body_table_validation",
    "deferred_until_fact_packet_validation",
    "deferred_until_trend_charts_required",
    "deferred_until_body_sections_required",
    "deferred_until_body_stage",
    "not_started",
    "pending",
}
EXPECTED_AGENT_PROVENANCE = {
    "project_profile": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
    "long_term": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
    "accelerated": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
    "stress_study": {
        "extraction_agent": "reference_fact_extraction_agent",
        "validation_agent": "reference_fact_validation_agent",
    },
}
PASSING_AGENT_VALIDATION_STATUSES = {"passed", "passed_with_warnings"}
REQUIRED_PROVENANCE_EXECUTION_MODE = "subagent"
REQUIRED_PROVENANCE_LIST_FIELDS = {"source_materials_reviewed", "validation_checks"}
REQUIRED_BASE_BODY_SECTION_KEYS = [
    "long_term_intro",
    "long_term_trend_intro",
    "long_term_trend_summary",
    "accelerated_intro",
    "accelerated_trend_intro",
    "accelerated_trend_summary",
    "final_stability_conclusion",
]
STRESS_BODY_SECTION_KEYS_BY_ROLE = {
    "light_stress": ["stress_light_reference", "stress_light_summary"],
    "agitation_stress": ["stress_agitation_summary"],
    "freeze_thaw_stress": ["stress_freeze_thaw_summary"],
    "high_temperature_stress": ["stress_high_temperature_summary"],
    "low_ph_stress": ["stress_ph_summary"],
    "high_ph_stress": ["stress_ph_summary"],
    "oxidation_stress": ["stress_oxidation_summary"],
}
BODY_SECTION_PATTERN_HINTS = {
    "long_term_intro": "真实结果稿经验沉淀.LONG_TERM_INTRO.ongoing",
    "long_term_trend_intro": "真实结果稿经验沉淀.LONG_TERM_TREND_INTRO.with_figures",
    "long_term_trend_summary": "真实结果稿经验沉淀.LONG_TERM_TREND_SUMMARY.stable",
    "accelerated_intro": "真实结果稿经验沉淀.ACCELERATED_INTRO.stable",
    "accelerated_trend_intro": "真实结果稿经验沉淀.ACCELERATED_TREND_INTRO.with_figures",
    "accelerated_trend_summary": "真实结果稿经验沉淀.ACCELERATED_TREND_SUMMARY.changed",
    "stress_study_intro": "真实结果稿经验沉淀.STRESS_STUDY_INTRO.actual_tests",
    "stress_light_reference": "真实结果稿经验沉淀.STRESS_LIGHT_REFERENCE",
    "stress_light_summary": "真实结果稿经验沉淀.STRESS_LIGHT_SUMMARY.sensitive",
    "stress_agitation_summary": "真实结果稿经验沉淀.STRESS_AGITATION_SUMMARY.no_change",
    "stress_freeze_thaw_summary": "真实结果稿经验沉淀.STRESS_FREEZE_THAW_SUMMARY.no_change",
    "stress_high_temperature_summary": "真实结果稿经验沉淀.STRESS_HIGH_TEMPERATURE_SUMMARY.changed",
    "stress_ph_summary": "真实结果稿经验沉淀.STRESS_PH_SUMMARY.split_low_high",
    "stress_oxidation_summary": "真实结果稿经验沉淀.STRESS_OXIDATION_SUMMARY.adc_or_antibody",
    "final_stability_conclusion": "真实结果稿经验沉淀.FINAL_STABILITY_CONCLUSION.actual_tests_only",
}
STRESS_ROLE_ALIASES = {
    "light_stress": ["光照", "light"],
    "agitation_stress": ["振荡", "震荡", "agitation", "shaking", "shake"],
    "freeze_thaw_stress": ["冻融", "反复冻融", "freeze", "thaw", "freeze_thaw"],
    "high_temperature_stress": ["高温", "high temperature", "high_temperature"],
    "low_ph_stress": ["ph", "pH", "PH", "低pH", "低ph", "酸碱", "low ph"],
    "high_ph_stress": ["ph", "pH", "PH", "高pH", "高ph", "酸碱", "high ph"],
    "oxidation_stress": ["氧化", "oxidation", "oxidative", "H2O2", "TBHP"],
}
STRESS_RENDER_ROLE_ORDER = [
    "light_stress",
    "agitation_stress",
    "freeze_thaw_stress",
    "high_temperature_stress",
    "low_ph_stress",
    "high_ph_stress",
    "oxidation_stress",
]
TABLE_BODY_REFERENCE_RE = re.compile(r"表\s*(?:3\.2\.S\.7\.3-\s*)?(\d+)")
FIGURE_BODY_REFERENCE_RE = re.compile(r"图\s*(?:3\.2\.S\.7\.3-\s*)?(\d+)")


def trend_chart_group(chart: dict[str, Any]) -> str:
    explicit = str(chart.get("study_key") or chart.get("group") or chart.get("section") or "").strip()
    if explicit:
        if explicit in {"long", "long_term", "长期", "长期稳定性"}:
            return "long_term"
        if explicit in {"accelerated", "accel", "加速", "加速稳定性"}:
            return "accelerated"
        if explicit in {"stress", "stress_study", "影响因素", "强制降解"}:
            return "stress_study"
    title = str(chart.get("title") or chart.get("caption") or "")
    lowered = title.lower()
    if "长期" in title or "long" in lowered:
        return "long_term"
    if "加速" in title or "accelerated" in lowered or "accel" in lowered:
        return "accelerated"
    if any(marker in title for marker in TREND_STRESS_MARKERS):
        return "stress_study"
    return ""


def parse_number(value: Any) -> float | None:
    try:
        if value in MISSING_MARKERS:
            return None
    except TypeError:
        return None
    text = str(value).strip()
    if text in MISSING_MARKERS or text.startswith(LIMIT_PREFIXES):
        return None
    cleaned = (
        text.replace(",", "")
        .replace("%", "")
        .replace("％", "")
        .replace("mg/ml", "")
        .replace("mg/mL", "")
        .replace("个月", "")
        .replace("月", "")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
        return float(match.group(0)) if match else None


def value_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value")
    return value


def normalize_text(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").lower())


def candidate_label(row: dict[str, Any]) -> str:
    item = str(row.get("item") or row.get("indicator") or "").strip()
    method = str(row.get("method") or "").strip()
    return item or method or "unnamed numeric item"


def writing_pattern_hint(section_key: Any) -> str:
    key = str(section_key or "").strip().lower()
    return BODY_SECTION_PATTERN_HINTS.get(key, f"真实结果稿经验沉淀.{str(section_key).upper()}")


def required_body_section_keys(packet: dict[str, Any]) -> list[str]:
    required = list(REQUIRED_BASE_BODY_SECTION_KEYS)
    stress = packet.get("stress_study")
    if not isinstance(stress, dict):
        return required
    inputs = stress.get("table_render_inputs")
    roles: set[str] = set()
    if isinstance(inputs, list):
        for table in inputs:
            if isinstance(table, dict):
                roles.add(str(table.get("role") or ""))
    included = stress.get("included_tests")
    if isinstance(included, list):
        for item in included:
            raw = json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item or "")
            normalized = normalize_text(raw)
            for role, aliases in STRESS_ROLE_ALIASES.items():
                if role in raw:
                    roles.add(role)
                    continue
                if any(normalize_text(alias) and normalize_text(alias) in normalized for alias in aliases):
                    roles.add(role)
    if roles or (isinstance(included, list) and included):
        required.append("stress_study_intro")
    for role in sorted(roles):
        for key in STRESS_BODY_SECTION_KEYS_BY_ROLE.get(role, []):
            if key not in required:
                required.append(key)
    return required


def collect_table_caption_numbers(packet: dict[str, Any]) -> dict[str, Any]:
    caption_no = 1
    by_study: dict[str, set[int]] = {key: set() for key in STUDY_KEYS}
    by_role: dict[str, set[int]] = {}
    all_numbers: set[int] = set()
    stress_role_rank = {role: idx for idx, role in enumerate(STRESS_RENDER_ROLE_ORDER)}
    for study_key in STUDY_KEYS:
        study = packet.get(study_key)
        if not isinstance(study, dict):
            continue
        inputs = study.get("table_render_inputs")
        if not isinstance(inputs, list):
            continue
        ordered_inputs = [table for table in inputs if isinstance(table, dict)]
        if study_key == "stress_study":
            ordered_inputs = sorted(
                enumerate(ordered_inputs),
                key=lambda item: (
                    stress_role_rank.get(str(item[1].get("role") or "").strip(), len(stress_role_rank)),
                    item[0],
                ),
            )
            ordered_inputs = [table for _, table in ordered_inputs]
        for table in ordered_inputs:
            if not isinstance(table, dict):
                continue
            role = str(table.get("role") or "").strip()
            by_study[study_key].add(caption_no)
            all_numbers.add(caption_no)
            if role:
                by_role.setdefault(role, set()).add(caption_no)
            caption_no += 1
    return {
        "all": all_numbers,
        "by_study": by_study,
        "by_role": by_role,
    }


def collect_figure_numbers(packet: dict[str, Any]) -> dict[str, set[int]]:
    trend_charts = packet.get("trend_charts")
    by_group: dict[str, set[int]] = {"long_term": set(), "accelerated": set(), "all": set()}
    if not isinstance(trend_charts, dict):
        return by_group
    charts = trend_charts.get("charts")
    if not isinstance(charts, list):
        return by_group
    for chart in charts:
        if not isinstance(chart, dict):
            continue
        try:
            figure_no = int(chart.get("figure_no"))
        except (TypeError, ValueError):
            continue
        by_group["all"].add(figure_no)
        group = trend_chart_group(chart)
        if group in {"long_term", "accelerated"}:
            by_group[group].add(figure_no)
    return by_group


def expected_table_numbers_for_section(packet: dict[str, Any], section_key: str, table_numbers: dict[str, Any]) -> set[int]:
    key = str(section_key or "").strip().lower()
    by_study = table_numbers.get("by_study", {})
    by_role = table_numbers.get("by_role", {})
    if key.startswith("long_term"):
        return set(by_study.get("long_term", set()))
    if key.startswith("accelerated"):
        return set(by_study.get("accelerated", set()))
    if key == "stress_study_intro":
        return set(by_study.get("stress_study", set()))
    if key.startswith("stress_light"):
        return set(by_role.get("light_stress", set()))
    if key.startswith("stress_agitation"):
        return set(by_role.get("agitation_stress", set()))
    if key.startswith("stress_freeze_thaw"):
        return set(by_role.get("freeze_thaw_stress", set()))
    if key.startswith("stress_high_temperature"):
        return set(by_role.get("high_temperature_stress", set()))
    if key.startswith("stress_ph"):
        return set(by_role.get("low_ph_stress", set())) | set(by_role.get("high_ph_stress", set()))
    if key.startswith("stress_oxidation"):
        return set(by_role.get("oxidation_stress", set()))
    if key == "final_stability_conclusion":
        return set(table_numbers.get("all", set()))
    return set()


def expected_figure_numbers_for_section(packet: dict[str, Any], section_key: str, figure_numbers: dict[str, set[int]]) -> set[int]:
    key = str(section_key or "").strip().lower()
    if key.startswith("long_term"):
        return set(figure_numbers.get("long_term", set()))
    if key.startswith("accelerated"):
        return set(figure_numbers.get("accelerated", set()))
    if key == "final_stability_conclusion":
        return set(figure_numbers.get("all", set()))
    return set()


def body_table_reference_numbers(text: str) -> list[int]:
    return [int(number) for number in TABLE_BODY_REFERENCE_RE.findall(text)]


def body_figure_reference_numbers(text: str) -> list[int]:
    return [int(number) for number in FIGURE_BODY_REFERENCE_RE.findall(text)]


def validate_body_section_reference_contract(packet: dict[str, Any], issues: list[dict[str, str]], require_coverage: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "required": require_coverage,
        "checked_sections": [],
        "table_reference_issues": [],
        "figure_reference_issues": [],
    }
    sections = body_sections(packet)
    if not isinstance(sections, dict) or not sections:
        return summary

    table_numbers = collect_table_caption_numbers(packet)
    figure_numbers = collect_figure_numbers(packet)

    for key, value in sections.items():
        path = f"body_sections.{key}.text"
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            text = str(value.get("text", value.get("content", "")) or "")
        else:
            continue
        if not text.strip():
            continue

        table_refs = sorted(set(body_table_reference_numbers(text)))
        figure_refs = sorted(set(body_figure_reference_numbers(text)))
        if not table_refs and not figure_refs:
            continue

        summary["checked_sections"].append(str(key))
        expected_tables = expected_table_numbers_for_section(packet, str(key), table_numbers)
        expected_figures = expected_figure_numbers_for_section(packet, str(key), figure_numbers)

        if table_refs:
            invalid_tables = [number for number in table_refs if number not in expected_tables]
            if invalid_tables:
                message = (
                    f"Table references {invalid_tables} do not match the rendered table numbers for this section; "
                    f"expected one of {sorted(expected_tables)}."
                )
                add_issue(issues, "error" if require_coverage else "warning", path, message)
                summary["table_reference_issues"].append({"path": path, "invalid": invalid_tables, "expected": sorted(expected_tables)})
            elif not expected_tables:
                message = "Section contains table references but the render plan does not define matching table numbers for this section."
                add_issue(issues, "error" if require_coverage else "warning", path, message)
                summary["table_reference_issues"].append({"path": path, "invalid": table_refs, "expected": []})

        if figure_refs:
            invalid_figures = [number for number in figure_refs if number not in expected_figures]
            if invalid_figures:
                message = (
                    f"Figure references {invalid_figures} do not match the generated trend figure numbers for this section; "
                    f"expected one of {sorted(expected_figures)}."
                )
                add_issue(issues, "error" if require_coverage else "warning", path, message)
                summary["figure_reference_issues"].append({"path": path, "invalid": invalid_figures, "expected": sorted(expected_figures)})
            elif not expected_figures:
                message = "Section contains figure references but the trend chart plan does not define matching figure numbers for this section."
                add_issue(issues, "error" if require_coverage else "warning", path, message)
                summary["figure_reference_issues"].append({"path": path, "invalid": figure_refs, "expected": []})

    return summary


def row_has_baseline_and_followup_numeric(row: dict[str, Any]) -> bool:
    values = row.get("values")
    if not isinstance(values, dict):
        return False
    has_baseline = False
    has_followup = False
    for point, raw_value in values.items():
        x = parse_number(point)
        y = parse_number(value_payload(raw_value))
        if x is None or y is None:
            continue
        if abs(x) < 1e-9:
            has_baseline = True
        elif x > 0:
            has_followup = True
    return has_baseline and has_followup


def trend_chart_candidates(packet: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, ...], dict[str, Any]] = {}
    for study_key in ["long_term", "accelerated"]:
        study = packet.get(study_key)
        if not isinstance(study, dict):
            continue
        condition = str(study.get("condition") or "")
        inputs = study.get("table_render_inputs")
        if not isinstance(inputs, list):
            continue
        for table_idx, table in enumerate(inputs):
            if not isinstance(table, dict):
                continue
            rows = table.get("rows")
            if not isinstance(rows, list):
                continue
            for row_idx, row in enumerate(rows):
                if not isinstance(row, dict) or not row_has_baseline_and_followup_numeric(row):
                    continue
                label = candidate_label(row)
                key = (study_key, normalize_text(condition), normalize_text(label))
                entry = candidates.setdefault(
                    key,
                    {
                        "study_key": study_key,
                        "condition": condition,
                        "item": label,
                        "batches": [],
                        "source_paths": [],
                    },
                )
                batch_no = str(table.get("batch_no") or "").strip()
                if batch_no and batch_no not in entry["batches"]:
                    entry["batches"].append(batch_no)
                entry["source_paths"].append(f"{study_key}.table_render_inputs[{table_idx}].rows[{row_idx}]")
    return list(candidates.values())


def chart_text_fields(chart: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ["title", "caption", "ylabel", "item", "test_item", "indicator", "source_ref", "body_section"]:
        if chart.get(key):
            values.append(str(chart[key]))
    for list_key in ["covered_items", "items", "test_items", "indicators"]:
        raw = chart.get(list_key)
        if isinstance(raw, list):
            values.extend(str(item) for item in raw)
        elif raw:
            values.append(str(raw))
    panels = chart.get("panels")
    if isinstance(panels, list):
        for panel in panels:
            if isinstance(panel, dict):
                for key in ["title", "caption", "ylabel", "item", "test_item", "indicator"]:
                    if panel.get(key):
                        values.append(str(panel[key]))
    return " ".join(values)


def chart_covers_candidate(chart: dict[str, Any], candidate: dict[str, Any]) -> bool:
    group = trend_chart_group(chart)
    if group != candidate.get("study_key"):
        return False
    item = normalize_text(candidate.get("item"))
    haystack = normalize_text(chart_text_fields(chart))
    return bool(item and item in haystack)


def manual_review_excludes_candidate(packet: dict[str, Any], candidate: dict[str, Any]) -> bool:
    item = normalize_text(candidate.get("item"))
    if not item:
        return False
    for entry in as_list(packet.get("manual_review_items")):
        text = json.dumps(entry, ensure_ascii=False) if isinstance(entry, (dict, list)) else str(entry)
        normalized = normalize_text(text)
        if item in normalized and any(marker in text.lower() for marker in TREND_EXCLUSION_MARKERS):
            return True
    return False


def series_numeric_summary(series: dict[str, Any]) -> tuple[bool, list[str]]:
    x_values = series.get("x")
    y_values = series.get("y")
    errors: list[str] = []
    if not isinstance(x_values, list) or not isinstance(y_values, list):
        return False, ["series must include x and y lists"]
    if len(x_values) != len(y_values):
        errors.append("series x and y lengths differ")
    has_baseline = False
    has_followup = False
    for x_raw, y_raw in zip(x_values, y_values):
        x = parse_number(x_raw)
        y = parse_number(y_raw)
        if x is None or y is None:
            continue
        if abs(x) < 1e-9:
            has_baseline = True
        elif x > 0:
            has_followup = True
    return has_baseline and has_followup, errors


def chart_has_numeric_baseline_and_followup(chart: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    matched = False
    series = chart.get("series")
    if isinstance(series, list):
        for item in series:
            if not isinstance(item, dict):
                errors.append("series entry must be an object")
                continue
            ok, item_errors = series_numeric_summary(item)
            errors.extend(item_errors)
            matched = matched or ok
    panels = chart.get("panels")
    if isinstance(panels, list):
        for panel in panels:
            if not isinstance(panel, dict):
                errors.append("panel entry must be an object")
                continue
            panel_series = panel.get("series")
            if not isinstance(panel_series, list):
                errors.append("panel must include a series list")
                continue
            for item in panel_series:
                if not isinstance(item, dict):
                    errors.append("panel series entry must be an object")
                    continue
                ok, item_errors = series_numeric_summary(item)
                errors.extend(item_errors)
                matched = matched or ok
    return matched, errors


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def add_issue(issues: list[dict[str, str]], level: str, path: str, message: str) -> None:
    issues.append({"level": level, "path": path, "message": message})


def has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def batch_no_from_entry(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("batch_no") or "").strip()
    return str(entry or "").strip()


def omitted_batch_reason(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("reason") or entry.get("omit_reason") or entry.get("status_reason") or "").strip()
    return ""


def project_batch_numbers(packet: dict[str, Any]) -> list[str]:
    profile = packet.get("project_profile")
    if not isinstance(profile, dict):
        return []
    batches = profile.get("batches")
    if not isinstance(batches, list):
        return []
    return [batch_no for item in batches if (batch_no := batch_no_from_entry(item))]


def render_scope_from_plan(plan: dict[str, Any]) -> dict[str, Any] | None:
    scope = plan.get("render_scope")
    return scope if isinstance(scope, dict) else None


def render_action(packet: dict[str, Any]) -> str:
    plan = packet.get("docx_render_plan")
    if not isinstance(plan, dict):
        return ""
    scope = render_scope_from_plan(plan)
    if scope is not None and scope.get("batch_structure_action"):
        return str(scope.get("batch_structure_action"))
    return str(plan.get("batch_count_action") or "")


def collect_table_render_inputs(packet: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for key in STUDY_KEYS:
        study = packet.get(key)
        if not isinstance(study, dict):
            continue
        inputs = study.get("table_render_inputs")
        if not isinstance(inputs, list):
            continue
        tables.extend(item for item in inputs if isinstance(item, dict))
    return tables


def body_sections(packet: dict[str, Any]) -> dict[str, Any]:
    sections = packet.get("body_sections")
    if isinstance(sections, dict):
        return sections
    plan = packet.get("docx_render_plan")
    if isinstance(plan, dict) and isinstance(plan.get("body_sections"), dict):
        return plan["body_sections"]
    return {}


def is_deferred_marker(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in DEFERRED_STATUS_MARKERS or text.startswith("deferred_until_")


def trend_charts_have_stage_content(packet: dict[str, Any]) -> bool:
    trend_charts = packet.get("trend_charts")
    if not isinstance(trend_charts, dict):
        return False
    charts = trend_charts.get("charts")
    return isinstance(charts, list) and bool(charts)


def body_sections_have_stage_text(packet: dict[str, Any]) -> bool:
    sections = body_sections(packet)
    for key, value in sections.items():
        key_text = str(key)
        if key_text in DEFERRED_METADATA_KEYS:
            if not is_deferred_marker(value):
                continue
            continue
        if isinstance(value, str):
            if value.strip() and not is_deferred_marker(value):
                return True
            continue
        if isinstance(value, dict):
            text = value.get("text", value.get("content"))
            if has_value(text) and not is_deferred_marker(text):
                return True
    return False


def validate_stage_boundaries(
    packet: dict[str, Any],
    issues: list[dict[str, str]],
    require_trend_charts: bool,
    require_body_sections: bool,
) -> None:
    if not require_trend_charts and trend_charts_have_stage_content(packet):
        add_issue(
            issues,
            "error",
            "trend_charts.charts",
            "Initial fact-packet stage must not include completed trend charts; submit table/body facts first, then add trend_charts in TREND_CHARTS_REQUIRED.",
        )
    if not require_body_sections and body_sections_have_stage_text(packet):
        add_issue(
            issues,
            "error",
            "body_sections",
            "Body section text must not be completed before BODY_SECTIONS_REQUIRED; keep body_sections empty or deferred until trend_charts validation passes.",
        )


def contains_pending_marker(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in ["pending_fact_extraction", "未找到", "无法确认", "not found", "not confirmed"])
    if isinstance(value, dict):
        return any(contains_pending_marker(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_pending_marker(child) for child in value)
    return False


def recovery_attempts(packet: dict[str, Any]) -> list[Any]:
    recovery = packet.get("missing_evidence_recovery")
    if isinstance(recovery, dict) and isinstance(recovery.get("attempts"), list):
        return recovery["attempts"]
    if isinstance(recovery, list):
        return recovery
    return []


def unresolved_manual_review_items(packet: dict[str, Any]) -> list[Any]:
    manual = packet.get("manual_review_items")
    if not isinstance(manual, list):
        return []
    unresolved = []
    for item in manual:
        if isinstance(item, dict):
            severity = str(item.get("severity") or "").lower()
            status = str(item.get("status") or "").lower()
            if severity in {"high", "critical", "blocking", "中", "高"} or status in {"open", "pending", "unresolved"}:
                unresolved.append(item)
        elif contains_pending_marker(item):
            unresolved.append(item)
    return unresolved


def unresolved_fact_state(packet: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if bool(packet.get("missing_evidence")):
        reasons.append("missing_evidence is not empty")
    if unresolved_manual_review_items(packet):
        reasons.append("manual_review_items contains unresolved or blocking items")
    if contains_pending_marker(packet):
        reasons.append("pending or not-found markers remain in fact packet")
    return bool(reasons), reasons


def validate_top_level(packet: dict[str, Any], issues: list[dict[str, str]]) -> None:
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in packet:
            add_issue(issues, "error", key, "Missing required top-level key.")
    if packet.get("schema_version") != SCHEMA_VERSION:
        add_issue(issues, "error", "schema_version", f"Expected {SCHEMA_VERSION!r}.")


def validate_agent_provenance(packet: dict[str, Any], issues: list[dict[str, str]]) -> None:
    provenance = packet.get("agent_provenance")
    if not isinstance(provenance, dict):
        add_issue(issues, "error", "agent_provenance", "Must record the four validated fact-shard sub-agent provenance entries.")
        return
    for section, expected in EXPECTED_AGENT_PROVENANCE.items():
        entry = provenance.get(section)
        path = f"agent_provenance.{section}"
        if not isinstance(entry, dict):
            add_issue(issues, "error", path, "Missing provenance for assembled fact shard.")
            continue
        if entry.get("extraction_agent") != expected["extraction_agent"]:
            add_issue(issues, "error", f"{path}.extraction_agent", f"Expected {expected['extraction_agent']!r}.")
        if entry.get("validation_agent") != expected["validation_agent"]:
            add_issue(issues, "error", f"{path}.validation_agent", f"Expected {expected['validation_agent']!r}.")
        status = str(entry.get("validation_status") or "").strip().lower()
        if status not in PASSING_AGENT_VALIDATION_STATUSES:
            add_issue(issues, "error", f"{path}.validation_status", f"Expected one of {sorted(PASSING_AGENT_VALIDATION_STATUSES)}.")
        execution_mode = str(entry.get("execution_mode") or "").strip()
        if execution_mode != REQUIRED_PROVENANCE_EXECUTION_MODE:
            add_issue(issues, "error", f"{path}.execution_mode", "Expected 'subagent'; same_thread_separated_pass is not accepted for renderable fact packets.")
        for key in sorted(REQUIRED_PROVENANCE_LIST_FIELDS):
            values = entry.get(key)
            if not isinstance(values, list) or not any(str(value).strip() for value in values):
                add_issue(issues, "error", f"{path}.{key}", "Must be a non-empty list for auditability.")


def validate_project_profile(packet: dict[str, Any], issues: list[dict[str, str]]) -> None:
    profile = packet.get("project_profile")
    if not isinstance(profile, dict):
        add_issue(issues, "error", "project_profile", "Must be an object.")
        return
    batches = profile.get("batches")
    if not isinstance(batches, list):
        add_issue(issues, "error", "project_profile.batches", "Must be a list; do not collapse batches into a scalar field.")
    else:
        seen_batches: set[str] = set()
        for idx, batch in enumerate(batches):
            if not isinstance(batch, dict):
                add_issue(issues, "error", f"project_profile.batches[{idx}]", "Batch entry must be an object.")
                continue
            batch_no = batch_no_from_entry(batch)
            if not has_value(batch_no):
                add_issue(issues, "error", f"project_profile.batches[{idx}].batch_no", "Batch entry must include batch_no.")
            elif batch_no in seen_batches:
                add_issue(issues, "error", f"project_profile.batches[{idx}].batch_no", f"Duplicate batch_no is not allowed: {batch_no}.")
            else:
                seen_batches.add(batch_no)
    for forbidden in ["ds_batch", "main_batch", "first_batch"]:
        if forbidden in profile:
            add_issue(issues, "warning", f"project_profile.{forbidden}", "Single-batch alias must not drive rendering; use project_profile.batches.")


def validate_sources(packet: dict[str, Any], issues: list[dict[str, str]]) -> None:
    sources = packet.get("sources")
    if not isinstance(sources, dict):
        add_issue(issues, "error", "sources", "Must be an object.")
        return
    allowed = sources.get("allowed")
    forbidden = sources.get("forbidden")
    if not isinstance(allowed, list):
        add_issue(issues, "error", "sources.allowed", "Must list every allowed source and its use status.")
    else:
        for idx, source in enumerate(allowed):
            if not isinstance(source, dict):
                add_issue(issues, "error", f"sources.allowed[{idx}]", "Source entry must be an object.")
                continue
            if not has_value(source.get("path")):
                add_issue(issues, "error", f"sources.allowed[{idx}].path", "Allowed source must include path.")
            if source.get("status") not in {"used", "partially_used", "unusable"}:
                add_issue(issues, "warning", f"sources.allowed[{idx}].status", "Expected used, partially_used, or unusable.")
    if not isinstance(forbidden, list):
        add_issue(issues, "error", "sources.forbidden", "Must list forbidden sources, including completed submissions and lock files when present.")
    else:
        for idx, source in enumerate(forbidden):
            if not isinstance(source, dict):
                add_issue(issues, "error", f"sources.forbidden[{idx}]", "Forbidden source entry must be an object.")
                continue
            if source.get("status") != "excluded":
                add_issue(issues, "warning", f"sources.forbidden[{idx}].status", "Forbidden source should be marked excluded.")


def validate_marker_semantics(study: dict[str, Any], path: str, issues: list[dict[str, str]]) -> None:
    semantics = study.get("marker_semantics")
    if not isinstance(semantics, dict):
        add_issue(issues, "error", f"{path}.marker_semantics", "Must describe N/A and --- conversion rules.")
        return
    if semantics.get("blank_within_completed_timepoint") != "N/A":
        add_issue(issues, "warning", f"{path}.marker_semantics.blank_within_completed_timepoint", "Expected N/A.")
    if semantics.get("blank_after_completed_timepoint") != "---":
        add_issue(issues, "warning", f"{path}.marker_semantics.blank_after_completed_timepoint", "Expected ---.")


def validate_value_object(value: Any, path: str, issues: list[dict[str, str]]) -> None:
    if isinstance(value, dict):
        if "value" not in value:
            add_issue(issues, "warning", f"{path}.value", "Value object should include value.")
        if value.get("marker") not in {"value", "N/A", "---", "source_marker", "missing", None}:
            add_issue(issues, "warning", f"{path}.marker", "Unexpected marker; use value, N/A, ---, source_marker, or missing.")
    elif value in {"N/A", "---", ""}:
        add_issue(issues, "warning", path, "Scalar marker is allowed for renderer compatibility, but fact-packet should preserve marker/source_ref details.")


def validate_row_shape(role: str, row: dict[str, Any], path: str, issues: list[dict[str, str]]) -> None:
    if role in STABILITY_ROLES:
        for key in ["group", "item", "method", "acceptance"]:
            if not has_value(row.get(key)):
                add_issue(issues, "warning", f"{path}.{key}", f"Stability rows should include {key}.")
    if role in STRESS_ROLES:
        if not has_value(row.get("item")):
            add_issue(issues, "error", f"{path}.item", "Stress-study rows must preserve the specific test item used for the first rendered column.")
        if not has_value(row.get("method")):
            add_issue(issues, "error", f"{path}.method", "Stress-study rows must preserve the test method so the first rendered column can be item plus method.")
        if not (has_value(row.get("acceptance")) or has_value(row.get("quality_standard")) or has_value(row.get("no_standard_reason"))):
            add_issue(issues, "error", f"{path}.acceptance", "Stress-study rows must include a quality standard in the second rendered column, or explicit no_standard_reason.")
        if has_value(row.get("indicator_method")) and (has_value(row.get("group")) or has_value(row.get("indicator"))):
            add_issue(issues, "warning", f"{path}.indicator_method", "Stress-study first rendered column should be item plus method; do not include grouping text unless the source row's item itself contains it.")


def validate_table_render_inputs(study: dict[str, Any], path: str, issues: list[dict[str, str]], batch_action: str) -> None:
    inputs = study.get("table_render_inputs")
    if not isinstance(inputs, list):
        add_issue(issues, "error", f"{path}.table_render_inputs", "Must be a list, even when no tables are rendered.")
        return
    for idx, table in enumerate(inputs):
        table_path = f"{path}.table_render_inputs[{idx}]"
        if not isinstance(table, dict):
            add_issue(issues, "error", table_path, "Table render input must be an object.")
            continue
        for key in REQUIRED_TABLE_KEYS:
            if key not in table:
                add_issue(issues, "error", f"{table_path}.{key}", "Missing required table render field.")
        role = str(table.get("role") or "")
        if not has_value(role):
            add_issue(issues, "error", f"{table_path}.role", "Table render input must include role.")
        if not has_value(table.get("batch_no")):
            add_issue(issues, "warning", f"{table_path}.batch_no", "Table render input should include batch_no for traceability.")
        time_header = table.get("time_header")
        if not isinstance(time_header, dict) or not isinstance(time_header.get("points"), list) or not time_header.get("points"):
            add_issue(issues, "error", f"{table_path}.time_header.points", "Must be a non-empty list of time points.")
        rows = table.get("rows")
        if not isinstance(rows, list):
            add_issue(issues, "error", f"{table_path}.rows", "Rows must be a list.")
            continue
        if role in STRESS_ROLES and not rows:
            add_issue(issues, "error", f"{table_path}.rows", "Rendered stress-study/influence-factor tables must not have empty rows; extract item, method, acceptance, and time-point values from the stress source.")
        for row_idx, row in enumerate(rows):
            if not isinstance(row, dict):
                add_issue(issues, "error", f"{table_path}.rows[{row_idx}]", "Row must be an object.")
                continue
            validate_row_shape(role, row, f"{table_path}.rows[{row_idx}]", issues)
            values = row.get("values")
            if not isinstance(values, dict):
                add_issue(issues, "error", f"{table_path}.rows[{row_idx}].values", "Values must map time point to value object or renderer-compatible scalar.")
                continue
            if isinstance(time_header, dict) and isinstance(time_header.get("points"), list):
                missing_points = [point for point in time_header["points"] if str(point) not in {str(existing) for existing in values}]
                if missing_points:
                    add_issue(issues, "warning", f"{table_path}.rows[{row_idx}].values", f"Missing values for time points: {missing_points}.")
            for point, value in values.items():
                validate_value_object(value, f"{table_path}.rows[{row_idx}].values[{point!r}]", issues)


def validate_studies(packet: dict[str, Any], issues: list[dict[str, str]]) -> None:
    batch_action = render_action(packet)
    for key in ["long_term", "accelerated"]:
        study = packet.get(key)
        if not isinstance(study, dict):
            add_issue(issues, "error", key, "Must be an object.")
            continue
        if not isinstance(study.get("planned_timepoints"), list):
            add_issue(issues, "error", f"{key}.planned_timepoints", "Must be a list.")
        if not isinstance(study.get("completed_timepoints_by_batch"), dict):
            add_issue(issues, "error", f"{key}.completed_timepoints_by_batch", "Must map batch_no to completed time points.")
        validate_marker_semantics(study, key, issues)
        validate_table_render_inputs(study, key, issues, batch_action)

    stress = packet.get("stress_study")
    if not isinstance(stress, dict):
        add_issue(issues, "error", "stress_study", "Must be an object.")
        return
    if not isinstance(stress.get("included_tests"), list):
        add_issue(issues, "error", "stress_study.included_tests", "Must be a list.")
    if not isinstance(stress.get("omitted_tests"), list):
        add_issue(issues, "warning", "stress_study.omitted_tests", "Should list omitted/not-performed stress tests.")
    validate_table_render_inputs(stress, "stress_study", issues, batch_action)


def validate_body_sections(packet: dict[str, Any], issues: list[dict[str, str]], require_coverage: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "required": require_coverage,
        "required_keys": required_body_section_keys(packet) if require_coverage else [],
        "provided_keys": [],
        "missing_keys": [],
    }
    raw = packet.get("body_sections")
    if raw is None:
        if require_coverage:
            add_issue(issues, "error", "body_sections", "Must be an object with completed semantic paragraph text.")
            summary["missing_keys"] = list(summary["required_keys"])
        return summary
    if not isinstance(raw, dict):
        add_issue(issues, "error", "body_sections", "Must be an object mapping section keys to text or section objects.")
        return summary
    summary["provided_keys"] = sorted(str(key) for key in raw)
    if require_coverage:
        missing_keys = [key for key in summary["required_keys"] if key not in raw]
        summary["missing_keys"] = missing_keys
        for key in missing_keys:
            add_issue(issues, "error", f"body_sections.{key}", "Required body section is missing.")
    for key, value in raw.items():
        path = f"body_sections.{key}"
        if isinstance(value, str):
            if not value.strip():
                add_issue(issues, "error" if require_coverage else "warning", path, "Section text is empty.")
            else:
                add_issue(
                    issues,
                    "error" if require_coverage else "warning",
                    path,
                    "Object form is preferred so source_refs and writing_pattern_refs can record evidence and drafting pattern coverage.",
                )
                for marker in BODY_SECTION_FORBIDDEN_TEXT_MARKERS:
                    if marker in value:
                        add_issue(issues, "error" if require_coverage else "warning", path, f"Section text contains unresolved template marker: {marker}.")
            continue
        if not isinstance(value, dict):
            add_issue(issues, "error", path, "Section must be a string or an object.")
            continue
        placeholder = str(value.get("placeholder") or "")
        if placeholder and not (placeholder.startswith("{{") and placeholder.endswith("}}")):
            add_issue(issues, "error" if require_coverage else "warning", f"{path}.placeholder", "Semantic paragraph placeholder should use {{...}} form.")
        text = value.get("text", value.get("content"))
        if not has_value(text):
            add_issue(issues, "error" if require_coverage else "warning", f"{path}.text", "Section text is empty.")
        elif isinstance(text, str):
            for marker in BODY_SECTION_FORBIDDEN_TEXT_MARKERS:
                if marker in text:
                    add_issue(issues, "error" if require_coverage else "warning", f"{path}.text", f"Section text contains unresolved template marker: {marker}.")
        source_refs = value.get("source_refs")
        if source_refs is None:
            add_issue(issues, "error" if require_coverage else "warning", f"{path}.source_refs", "Should record source_refs for paragraph facts.")
        elif not isinstance(source_refs, list):
            add_issue(issues, "warning", f"{path}.source_refs", "Should be a list when provided.")
        elif require_coverage and not source_refs:
            add_issue(issues, "error", f"{path}.source_refs", "Must be a non-empty list for completed body sections.")
        pattern_refs = None
        for ref_key in BODY_SECTION_PATTERN_REF_KEYS:
            if ref_key in value:
                pattern_refs = value.get(ref_key)
                break
        if pattern_refs is None:
            add_issue(
                issues,
                "error" if require_coverage else "warning",
                path,
                f"Record writing_pattern_refs such as {writing_pattern_hint(key)} to show the paragraph used the writing patterns.",
            )
        elif not isinstance(pattern_refs, list) or not pattern_refs:
            add_issue(issues, "error" if require_coverage else "warning", f"{path}.writing_pattern_refs", "Should be a non-empty list when provided.")
    return summary


def validate_trend_charts(packet: dict[str, Any], issues: list[dict[str, str]], require_coverage: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "required": require_coverage,
        "chart_count": 0,
        "candidate_count": 0,
        "uncovered_candidates": [],
    }
    trend_charts = packet.get("trend_charts")
    if trend_charts is None:
        return summary
    if not isinstance(trend_charts, dict):
        add_issue(issues, "error", "trend_charts", "Must be an object with a charts list.")
        return summary
    charts = trend_charts.get("charts")
    if not isinstance(charts, list):
        if not require_coverage and is_deferred_marker(trend_charts.get("status") or trend_charts.get("deferred_until")):
            return summary
        add_issue(issues, "error", "trend_charts.charts", "Must be a list, even when no charts are generated.")
        return summary
    summary["chart_count"] = len(charts)

    figure_numbers: set[int] = set()
    for chart_idx, chart in enumerate(charts):
        chart_path = f"trend_charts.charts[{chart_idx}]"
        if not isinstance(chart, dict):
            add_issue(issues, "error" if require_coverage else "warning", chart_path, "Chart entry must be an object.")
            continue
        try:
            figure_no = int(chart.get("figure_no"))
        except (TypeError, ValueError):
            figure_no = None
        if figure_no is None:
            add_issue(issues, "error" if require_coverage else "warning", f"{chart_path}.figure_no", "Chart should record an integer figure_no.")
        elif figure_no in figure_numbers:
            add_issue(issues, "error" if require_coverage else "warning", f"{chart_path}.figure_no", f"Duplicate trend chart figure_no: {figure_no}.")
        else:
            figure_numbers.add(figure_no)
        if not has_value(chart.get("title")):
            add_issue(issues, "error" if require_coverage else "warning", f"{chart_path}.title", "Chart should record the DOCX figure title.")
        if not has_value(chart.get("filename")):
            add_issue(issues, "error" if require_coverage else "warning", f"{chart_path}.filename", "Chart should record the generated PNG filename.")
        group = trend_chart_group(chart)
        if group == "stress_study":
            add_issue(
                issues,
                "error" if require_coverage else "warning",
                chart_path,
                "Influence-factor studies do not render trend figures; remove stress/影响因素 charts from trend_charts.",
            )
        elif not group:
            add_issue(issues, "error" if require_coverage else "warning", chart_path, "Chart should declare study_key/group/section as long_term or accelerated, or include 长期/加速 in the title.")
        series = chart.get("series")
        panels = chart.get("panels")
        if series is None and panels is None:
            add_issue(issues, "error" if require_coverage else "warning", chart_path, "Chart should include series or panels before generation.")
        if series is not None and not isinstance(series, list):
            add_issue(issues, "error" if require_coverage else "warning", f"{chart_path}.series", "Must be a list when provided.")
        if panels is not None and not isinstance(panels, list):
            add_issue(issues, "error" if require_coverage else "warning", f"{chart_path}.panels", "Must be a list when provided.")
        if require_coverage and (isinstance(series, list) or isinstance(panels, list)):
            has_numeric_trend, chart_errors = chart_has_numeric_baseline_and_followup(chart)
            for error in chart_errors:
                add_issue(issues, "error", chart_path, error)
            if not has_numeric_trend:
                add_issue(
                    issues,
                    "error",
                    chart_path,
                    "Trend chart must include at least one numeric series with 0 month and a later time point.",
                )

    candidates = trend_chart_candidates(packet)
    summary["candidate_count"] = len(candidates)
    if require_coverage:
        for candidate in candidates:
            if any(isinstance(chart, dict) and chart_covers_candidate(chart, candidate) for chart in charts):
                continue
            if manual_review_excludes_candidate(packet, candidate):
                continue
            summary["uncovered_candidates"].append(candidate)
            source_paths = ", ".join(candidate.get("source_paths") or [])
            add_issue(
                issues,
                "error",
                "trend_charts.charts",
                (
                    f"Missing trend chart coverage for {candidate.get('study_key')} item "
                    f"{candidate.get('item')!r}; numeric 0 month plus follow-up data exist in {source_paths}."
                ),
            )
        if candidates and not charts and not summary["uncovered_candidates"]:
            add_issue(
                issues,
                "warning",
                "trend_charts.charts",
                "Qualifying numeric stability data were excluded by manual_review_items; confirm the exclusion is source-supported.",
            )
    return summary


def validate_render_plan(packet: dict[str, Any], issues: list[dict[str, str]], require_body_sections: bool = False) -> None:
    plan = packet.get("docx_render_plan")
    if not isinstance(plan, dict):
        add_issue(issues, "error", "docx_render_plan", "Must be an object.")
        return
    if not has_value(plan.get("template_id")):
        add_issue(issues, "error", "docx_render_plan.template_id", "Must record active template ID.")
    batch_count_action = plan.get("batch_count_action")
    if batch_count_action not in VALID_BATCH_STRUCTURE_ACTIONS | {None}:
        add_issue(issues, "error", "docx_render_plan.batch_count_action", "Expected keep, shrink, or expand.")
    prose_action = str(plan.get("body_prose_action") or plan.get("prose_action") or ("body_sections" if body_sections(packet) else "project_specific_rewrite"))
    if prose_action in {"body_sections", "semantic_placeholders"} and not body_sections(packet):
        add_issue(
            issues,
            "error" if require_body_sections else "warning",
            "body_sections",
            "body_prose_action requires semantic body_sections.",
        )

    project_batches = project_batch_numbers(packet)
    project_batch_set = set(project_batches)
    scope = render_scope_from_plan(plan)
    rendered_batch_numbers: list[str] = []
    omitted_batch_numbers: list[str] = []
    if scope is None:
        add_issue(issues, "error", "docx_render_plan.render_scope", "Must declare source_batch_count, rendered_batches, omitted_batches, and batch_structure_action.")
    else:
        source_batch_count = scope.get("source_batch_count")
        if not isinstance(source_batch_count, int):
            add_issue(issues, "error", "docx_render_plan.render_scope.source_batch_count", "Must be an integer count of all source-confirmed batches.")
        elif source_batch_count != len(project_batches):
            add_issue(
                issues,
                "error",
                "docx_render_plan.render_scope.source_batch_count",
                f"Must equal project_profile.batches count ({len(project_batches)}); facts must not be truncated or padded for the template.",
            )

        rendered = scope.get("rendered_batches")
        omitted = scope.get("omitted_batches")
        if not isinstance(rendered, list):
            add_issue(issues, "error", "docx_render_plan.render_scope.rendered_batches", "Must be a list of rendered batch numbers or batch objects.")
            rendered = []
        if not isinstance(omitted, list):
            add_issue(issues, "error", "docx_render_plan.render_scope.omitted_batches", "Must be a list, even when no source batches are omitted from rendering.")
            omitted = []
        rendered_batch_numbers = [batch_no for item in rendered if (batch_no := batch_no_from_entry(item))]
        omitted_batch_numbers = [batch_no for item in omitted if (batch_no := batch_no_from_entry(item))]

        for idx, item in enumerate(rendered):
            if not batch_no_from_entry(item):
                add_issue(issues, "error", f"docx_render_plan.render_scope.rendered_batches[{idx}]", "Rendered batch entry must include batch_no.")
        for idx, item in enumerate(omitted):
            batch_no = batch_no_from_entry(item)
            if not batch_no:
                add_issue(issues, "error", f"docx_render_plan.render_scope.omitted_batches[{idx}]", "Omitted batch entry must include batch_no.")
            if not omitted_batch_reason(item):
                add_issue(issues, "error", f"docx_render_plan.render_scope.omitted_batches[{idx}].reason", "Omitted source batches require an explicit reason.")

        duplicate_rendered = sorted({batch for batch in rendered_batch_numbers if rendered_batch_numbers.count(batch) > 1})
        duplicate_omitted = sorted({batch for batch in omitted_batch_numbers if omitted_batch_numbers.count(batch) > 1})
        for batch in duplicate_rendered:
            add_issue(issues, "error", "docx_render_plan.render_scope.rendered_batches", f"Duplicate rendered batch: {batch}.")
        for batch in duplicate_omitted:
            add_issue(issues, "error", "docx_render_plan.render_scope.omitted_batches", f"Duplicate omitted batch: {batch}.")

        rendered_set = set(rendered_batch_numbers)
        omitted_set = set(omitted_batch_numbers)
        overlap = sorted(rendered_set & omitted_set)
        missing = sorted(project_batch_set - rendered_set - omitted_set)
        extras = sorted((rendered_set | omitted_set) - project_batch_set)
        if overlap:
            add_issue(issues, "error", "docx_render_plan.render_scope", f"Batches cannot be both rendered and omitted: {overlap}.")
        if missing:
            add_issue(issues, "error", "docx_render_plan.render_scope", f"Every project batch must be rendered or explicitly omitted: missing {missing}.")
        if extras:
            add_issue(issues, "error", "docx_render_plan.render_scope", f"Render scope references batches not in project_profile.batches: {extras}.")

        action = scope.get("batch_structure_action")
        if action not in VALID_BATCH_STRUCTURE_ACTIONS:
            add_issue(issues, "error", "docx_render_plan.render_scope.batch_structure_action", "Expected keep, shrink, or expand.")
        else:
            rendered_count = len(rendered_batch_numbers)
            expected_action = "shrink" if rendered_count == 1 else "keep" if rendered_count == 2 else "expand" if rendered_count > 2 else ""
            if not expected_action:
                add_issue(issues, "error", "docx_render_plan.render_scope.rendered_batches", "At least one batch must be rendered.")
            elif action != expected_action:
                add_issue(
                    issues,
                    "error",
                    "docx_render_plan.render_scope.batch_structure_action",
                    f"Must be {expected_action!r} for {rendered_count} rendered batch(es).",
                )
            if batch_count_action is not None and batch_count_action != action:
                add_issue(
                    issues,
                    "error",
                    "docx_render_plan.batch_count_action",
                    "Compatibility alias conflicts with render_scope.batch_structure_action.",
                )

    rendered_set = set(rendered_batch_numbers)
    if rendered_set:
        for idx, table in enumerate(collect_table_render_inputs(packet)):
            batch_no = str(table.get("batch_no") or "")
            if batch_no and batch_no not in rendered_set:
                add_issue(
                    issues,
                    "error",
                    f"table_render_inputs[{idx}].batch_no",
                    f"Rendered table references batch outside render_scope.rendered_batches: {batch_no}.",
                )
    for key in ["table_actions", "caption_actions", "manual_refresh_items"]:
        if not isinstance(plan.get(key), list):
            add_issue(issues, "warning", f"docx_render_plan.{key}", "Should be a list.")


def validate_missing_evidence_recovery(packet: dict[str, Any], issues: list[dict[str, str]], max_recovery_attempts: int) -> None:
    has_unresolved, _ = unresolved_fact_state(packet)
    if not has_unresolved:
        return

    attempts = recovery_attempts(packet)
    if not attempts:
        add_issue(
            issues,
            "error",
            "missing_evidence_recovery.attempts",
            "Unresolved or pending facts require at least one recorded recovery attempt before DOCX rendering.",
        )
        return

    if len(attempts) > max_recovery_attempts:
        add_issue(
            issues,
            "error",
            "missing_evidence_recovery.attempts",
            f"Too many recovery attempts recorded; stop after {max_recovery_attempts} attempts and report remaining gaps.",
        )

    for idx, attempt in enumerate(attempts):
        path = f"missing_evidence_recovery.attempts[{idx}]"
        if not isinstance(attempt, dict):
            add_issue(issues, "error", path, "Recovery attempt must be an object.")
            continue
        for key in ["missing_fields", "searched_sources", "result"]:
            if not has_value(attempt.get(key)):
                add_issue(issues, "error", f"{path}.{key}", "Recovery attempt must record missing fields, searched sources, and result.")


def validate_packet(
    packet: dict[str, Any],
    max_recovery_attempts: int = 2,
    require_trend_charts: bool = False,
    require_body_sections: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    validate_top_level(packet, issues)
    validate_agent_provenance(packet, issues)
    validate_project_profile(packet, issues)
    validate_sources(packet, issues)
    validate_studies(packet, issues)
    validate_stage_boundaries(packet, issues, require_trend_charts, require_body_sections)
    body_section_validation = validate_body_sections(packet, issues, require_coverage=require_body_sections)
    body_section_reference_validation = validate_body_section_reference_contract(packet, issues, require_coverage=require_body_sections)
    trend_chart_validation = validate_trend_charts(packet, issues, require_coverage=require_trend_charts)
    validate_render_plan(packet, issues, require_body_sections=require_body_sections)
    validate_missing_evidence_recovery(packet, issues, max_recovery_attempts)
    for key in ["missing_evidence", "manual_review_items"]:
        if key in packet and not isinstance(packet.get(key), list):
            add_issue(issues, "error", key, "Must be a list.")
    errors = [issue for issue in issues if issue["level"] == "error"]
    warnings = [issue for issue in issues if issue["level"] == "warning"]
    has_unresolved_facts, unresolved_reasons = unresolved_fact_state(packet)
    attempts = recovery_attempts(packet)
    blocking_reasons = list(unresolved_reasons)
    blocking_reasons.extend(issue["message"] for issue in errors)
    render_blocking = bool(errors) or (has_unresolved_facts and len(attempts) >= max_recovery_attempts)
    if has_unresolved_facts and len(attempts) >= max_recovery_attempts:
        blocking_reasons.append("unresolved facts remain after reaching the recovery attempt limit")
    return {
        "schema_version": packet.get("schema_version"),
        "expected_schema_version": SCHEMA_VERSION,
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
        "has_unresolved_facts": has_unresolved_facts,
        "recovery_attempt_count": len(attempts),
        "max_recovery_attempts": max_recovery_attempts,
        "require_trend_charts": require_trend_charts,
        "require_body_sections": require_body_sections,
        "body_section_validation": body_section_validation,
        "body_section_reference_validation": body_section_reference_validation,
        "trend_chart_validation": trend_chart_validation,
        "render_blocking": render_blocking,
        "blocking_reasons": blocking_reasons,
    }


def main() -> None:
    require_internal_context("validate_fact_packet.py")
    parser = argparse.ArgumentParser(description="Validate CTD 3.2.S.7.3 fact-packet.json structure.")
    parser.add_argument("fact_packet", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--max-recovery-attempts", type=int, default=2)
    parser.add_argument("--require-trend-charts", action="store_true")
    parser.add_argument("--require-body-sections", action="store_true")
    args = parser.parse_args()

    packet = json.loads(args.fact_packet.read_text(encoding="utf-8"))
    report = validate_packet(
        packet,
        max_recovery_attempts=args.max_recovery_attempts,
        require_trend_charts=args.require_trend_charts,
        require_body_sections=args.require_body_sections,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["passed"] else 1)


# Internal module — do not invoke directly. Use skill_step.py as the public entry point.
if __name__ == "__main__":
    main()

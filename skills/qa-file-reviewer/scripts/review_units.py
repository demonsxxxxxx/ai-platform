#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build compact review units and bilingual pairs from document_map.json."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable


ZH_RE = re.compile(r"[\u4e00-\u9fff]")
EN_RE = re.compile(r"[A-Za-z]")
REFERENCE_RE = re.compile(r"\b[A-Z]{1,4}\([A-Z]\)-IP\d{3}[A-Z]?-P-\d{3}(?:-R\d{2})?\b")
FORMULA_RE = re.compile(r"[=±×÷*/]|[|｜][^|｜]{1,80}[|｜]")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|℃|°C|h|min|mL|L|mg|g|µm|μm|M|mM|U)")


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_kind(text: str) -> str:
    has_zh = bool(ZH_RE.search(text))
    has_en = bool(EN_RE.search(text))
    if has_zh and has_en:
        return "mixed"
    if has_zh:
        return "zh"
    if has_en:
        return "en"
    return "other"


def _unit_id(record: dict[str, Any], fallback_index: int) -> str:
    paragraph_id = str(record.get("paragraph_id") or "").strip()
    if paragraph_id:
        return paragraph_id

    xml_index = _as_int(record.get("xml_index"))
    if xml_index is not None:
        return f"xml-{xml_index:05d}"

    return f"u-{fallback_index:05d}"


def build_review_units(document_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one compact review unit per non-empty paragraph record."""

    paragraphs = document_map.get("paragraphs", [])
    if not isinstance(paragraphs, list):
        raise ValueError("document_map.paragraphs must be a list")

    units: list[dict[str, Any]] = []
    for index, record in enumerate(paragraphs, start=1):
        if not isinstance(record, dict):
            continue

        text = str(record.get("text") or "").strip()
        if not text:
            continue

        units.append(
            {
                "unit_id": _unit_id(record, index),
                "paragraph_id": str(record.get("paragraph_id") or "").strip(),
                "logical_index": _as_int(record.get("logical_index")),
                "xml_index": _as_int(record.get("xml_index")),
                "zone": str(record.get("document_zone") or "body").strip() or "body",
                "table_index": _as_int(record.get("table_index")),
                "row_index": _as_int(record.get("row_index")),
                "cell_index": _as_int(record.get("cell_index")),
                "text_kind": _text_kind(text),
                "text": text,
            }
        )

    return units


def _location_hint(unit: dict[str, Any]) -> str:
    if (
        unit.get("zone") == "table"
        and unit.get("table_index") is not None
        and unit.get("row_index") is not None
        and unit.get("cell_index") is not None
    ):
        return f"table T{unit['table_index']}R{unit['row_index']}C{unit['cell_index']}"
    return str(unit.get("zone") or "body")


def _source_hash(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def classify_review_domains(unit: dict[str, Any]) -> list[str]:
    text = str(unit.get("text") or "")
    domains: set[str] = set()

    if unit.get("zone") == "table":
        domains.add("table_data")

    text_kind = unit.get("text_kind")
    if text_kind == "zh":
        domains.add("chinese")
    elif text_kind == "en":
        domains.add("english")
    elif text_kind == "mixed":
        domains.update({"chinese", "english", "bilingual_consistency", "semantic_consistency"})

    style_name = str(unit.get("style_name") or "")
    if "heading" in style_name.lower() or re.match(r"^\s*\d+(?:\.\d+)*\s+\S+", text):
        domains.add("section_title")
    if REFERENCE_RE.search(text) or "参考文献" in text or "References" in text:
        domains.add("reference")
    if FORMULA_RE.search(text):
        domains.add("formula")
    if NUMBER_RE.search(text):
        domains.add("numeric_consistency")
    if any(token in text for token in ("IP", "批号", "Batch", "样品", "Sample", "项目", "Project")):
        domains.add("semantic_consistency")

    if not domains:
        domains.add("semantic_consistency")
    return sorted(domains)


def _compact_join(values: list[str], *, limit: int = 120) -> str:
    text = " | ".join(value.strip() for value in values if value and value.strip())
    return text[:limit]


def _table_hints(base_units: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    by_table_row: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    by_table_col: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for unit in base_units:
        if unit.get("zone") != "table":
            continue
        by_table_row.setdefault((unit.get("table_index"), unit.get("row_index")), []).append(unit)
        by_table_col.setdefault((unit.get("table_index"), unit.get("cell_index")), []).append(unit)

    sorted_rows = {
        key: sorted(
            row_units,
            key=lambda item: (item.get("cell_index") is None, item.get("cell_index") or 0),
        )
        for key, row_units in by_table_row.items()
    }
    sorted_cols = {
        key: sorted(
            col_units,
            key=lambda item: (item.get("row_index") is None, item.get("row_index") or 0),
        )
        for key, col_units in by_table_col.items()
    }
    header_by_col: dict[tuple[Any, Any], str] = {}
    for key, col_units in sorted_cols.items():
        header_text = ""
        for candidate in col_units:
            if candidate.get("row_index") == 1:
                header_text = str(candidate.get("text") or "").strip()
                break
        header_by_col[key] = header_text

    hints: dict[str, dict[str, str]] = {}
    for unit in base_units:
        if unit.get("zone") != "table":
            hints[unit["unit_id"]] = {"row_hint": "", "col_hint": ""}
            continue

        row_units = sorted_rows.get((unit.get("table_index"), unit.get("row_index")), [])
        header_text = header_by_col.get((unit.get("table_index"), unit.get("cell_index")), "")
        if unit.get("row_index") == 1 and header_text == str(unit.get("text") or "").strip():
            header_text = ""

        hints[unit["unit_id"]] = {
            "row_hint": _compact_join([str(item.get("text") or "") for item in row_units]),
            "col_hint": header_text[:80],
        }
    return hints


def _is_section_title(unit: dict[str, Any], review_domains: list[str]) -> bool:
    return "section_title" in review_domains


def _locator_safety(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", str(text or ""))
    short_anchor_risk = len(compact) <= 3 or bool(re.fullmatch(r"\d+(?:\.\d+)?", compact))
    return {
        "primary_rule": "anchor_quote must be copied from text",
        "short_anchor_risk": short_anchor_risk,
    }


def build_review_units_v2(document_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Return review units enriched for locator-first agent review context."""

    base_units = build_review_units(document_map)
    style_by_unit_id: dict[str, str] = {}
    paragraphs = document_map.get("paragraphs", [])
    if isinstance(paragraphs, list):
        for index, record in enumerate(paragraphs, start=1):
            if not isinstance(record, dict):
                continue
            text = str(record.get("text") or "").strip()
            if not text:
                continue
            style_name = str(record.get("style_name") or "").strip()
            if style_name:
                style_by_unit_id[_unit_id(record, index)] = style_name

    by_cell: dict[tuple[Any, Any, Any], list[str]] = {}
    for unit in base_units:
        if unit.get("zone") == "table":
            key = (unit.get("table_index"), unit.get("row_index"), unit.get("cell_index"))
            by_cell.setdefault(key, []).append(unit["unit_id"])

    hints = _table_hints(base_units)
    result: list[dict[str, Any]] = []
    current_section = ""
    current_block_key: tuple[Any, ...] | None = None
    current_block_id = ""
    block_counter = 0
    section_path: list[str] = []
    for index, unit in enumerate(base_units):
        neighbor_ids: list[str] = []
        if index > 0:
            neighbor_ids.append(base_units[index - 1]["unit_id"])
        if index + 1 < len(base_units):
            neighbor_ids.append(base_units[index + 1]["unit_id"])

        same_cell_ids: list[str] = []
        if unit.get("zone") == "table":
            key = (unit.get("table_index"), unit.get("row_index"), unit.get("cell_index"))
            same_cell_ids = [unit_id for unit_id in by_cell.get(key, []) if unit_id != unit["unit_id"]]

        unit_hints = hints.get(unit["unit_id"], {})
        enriched = dict(unit)
        if unit["unit_id"] in style_by_unit_id:
            enriched["style_name"] = style_by_unit_id[unit["unit_id"]]
        enriched.update(
            {
                "schema_version": "qa-review-context-unit.v2.2",
                "location_hint": _location_hint(unit),
                "neighbor_unit_ids": neighbor_ids,
                "same_cell_unit_ids": same_cell_ids,
                "section_hint": "",
                "section_path": [],
                "row_hint": unit_hints.get("row_hint", ""),
                "col_hint": unit_hints.get("col_hint", ""),
                "source_hash": _source_hash(str(unit.get("text") or "")),
                "locator_safety": _locator_safety(str(unit.get("text") or "")),
            }
        )
        review_domains = classify_review_domains(enriched)
        if _is_section_title(enriched, review_domains):
            title = str(unit.get("text") or "").strip()
            if title:
                section_path = [title]
                current_section = title
        enriched["section_hint"] = current_section
        enriched["section_path"] = list(section_path)
        enriched["review_domains"] = review_domains

        if unit.get("zone") == "table":
            block_key = ("table", unit.get("table_index"), unit.get("row_index"))
        elif "section_title" in review_domains:
            block_key = ("section", unit["unit_id"])
        else:
            block_key = ("body", current_section)

        if block_key != current_block_key:
            block_counter += 1
            current_block_key = block_key
            current_block_id = f"b-{block_counter:05d}"
        enriched["block_id"] = current_block_id
        result.append(enriched)
    return result


def _same_cell(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("zone") == "table"
        and right.get("zone") == "table"
        and left.get("table_index") is not None
        and left.get("table_index") == right.get("table_index")
        and left.get("row_index") == right.get("row_index")
        and left.get("cell_index") == right.get("cell_index")
    )


def _adjacent_xml(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_xml = left.get("xml_index")
    right_xml = right.get("xml_index")
    if not isinstance(left_xml, int) or not isinstance(right_xml, int):
        return False
    return 0 <= right_xml - left_xml <= 2


def build_bilingual_pairs(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect light-weight adjacent Chinese-to-English unit pairs."""

    pairs: list[dict[str, Any]] = []
    for left, right in zip(units, units[1:]):
        if left.get("text_kind") != "zh" or right.get("text_kind") != "en":
            continue

        same_cell = _same_cell(left, right)
        if same_cell:
            pair_type = "same_cell_adjacent"
        elif _adjacent_xml(left, right):
            pair_type = "adjacent"
        else:
            continue

        pairs.append(
            {
                "pair_id": f"bp-{len(pairs) + 1:05d}",
                "pair_type": pair_type,
                "zh_unit_id": left["unit_id"],
                "en_unit_id": right["unit_id"],
                "zh_text": left["text"],
                "en_text": right["text"],
                "table_index": left.get("table_index") if same_cell else None,
                "row_index": left.get("row_index") if same_cell else None,
                "cell_index": left.get("cell_index") if same_cell else None,
            }
        )

    return pairs


def _display(value: Any) -> str:
    return "?" if value is None or value == "" else str(value)


def format_context_line(unit: dict[str, Any]) -> str:
    """Format one agent-facing review context line."""

    parts = [
        f"u:{unit['unit_id']}",
        f"P{_display(unit.get('logical_index'))}",
        f"XML:{_display(unit.get('xml_index'))}",
    ]
    table = unit.get("table_index")
    row = unit.get("row_index")
    cell = unit.get("cell_index")
    zone = str(unit.get("zone") or "body")
    if table is not None and row is not None and cell is not None:
        parts.append(f"{zone} T{table}R{row}C{cell}")
    else:
        parts.append(zone)
    return f"[{' | '.join(parts)}] {unit['text']}"


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

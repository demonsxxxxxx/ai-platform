#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate agent-facing QA review context package completeness."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


UNIT_HEADING_RE = re.compile(r"^### UNIT\s+(\S+)\s*$")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_number}: invalid JSONL: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{line_number}: JSONL row must be an object")
            rows.append(row)
    return rows


def _relative_file(root: Path, value: Any, field_name: str, errors: list[str]) -> Path | None:
    name = str(value or "").strip()
    if not name:
        errors.append(f"manifest missing {field_name}")
        return None
    path = root / name
    if not path.exists():
        errors.append(f"missing {field_name.replace('_', ' ')}: {name}")
        return None
    return path


def _source_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_context_part(path: Path) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    current_unit_id = ""
    text_lines: list[str] = []
    in_text = False

    def flush() -> None:
        nonlocal current_unit_id, text_lines, in_text
        if current_unit_id:
            units.append({"unit_id": current_unit_id, "text": "\n".join(text_lines).strip()})
        current_unit_id = ""
        text_lines = []
        in_text = False

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        heading = UNIT_HEADING_RE.match(raw_line.strip())
        if heading:
            flush()
            current_unit_id = heading.group(1)
            continue
        if not current_unit_id:
            continue
        if raw_line.strip() == "TEXT:":
            text_lines = []
            in_text = True
            continue
        if raw_line.strip() == "ANCHOR_RULE:":
            in_text = False
            continue
        if in_text:
            text_lines.append(raw_line)

    flush()
    return units


def _block_unit_ids(row: dict[str, Any]) -> list[str]:
    if isinstance(row.get("unit_ids"), list):
        return [str(unit_id).strip() for unit_id in row["unit_ids"] if str(unit_id).strip()]
    unit_id = str(row.get("unit_id") or "").strip()
    return [unit_id] if unit_id else []


def _is_v21_block(row: dict[str, Any]) -> bool:
    return (
        row.get("schema_version") in {"qa-review-context-block.v2.1", "qa-review-context-block.v2.2"}
        or "block_kind" in row
        or "review_domains" in row
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _metrics_from_units(unit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    domain_counts: dict[str, int] = {}
    for row in unit_rows:
        for domain in row.get("review_domains") or []:
            domain_key = str(domain)
            domain_counts[domain_key] = domain_counts.get(domain_key, 0) + 1

    return {
        "domain_counts": domain_counts,
        "table_unit_count": sum(1 for row in unit_rows if row.get("zone") == "table"),
        "body_unit_count": sum(1 for row in unit_rows if row.get("zone") != "table"),
        "short_anchor_risk_count": sum(
            1
            for row in unit_rows
            if isinstance(row.get("locator_safety"), dict)
            and row["locator_safety"].get("short_anchor_risk")
        ),
    }


def validate_package(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = _load_json(manifest_path)
    root = manifest_path.parent
    is_locator_v22 = manifest.get("context_profile") == "locator-v2.2"

    if manifest.get("schema_version") != "qa-review-context-package.v2":
        errors.append("manifest schema_version must be qa-review-context-package.v2")

    context_parts = manifest.get("context_parts")
    if not isinstance(context_parts, list) or not context_parts:
        errors.append("manifest context_parts must be a non-empty list")
        context_parts = []

    context_units: list[dict[str, str]] = []
    context_units_by_part: dict[str, list[str]] = {}
    for part_name in context_parts:
        part_path = root / str(part_name)
        if not part_path.exists():
            errors.append(f"missing context part: {part_name}")
            continue
        parsed_units = _parse_context_part(part_path)
        context_units.extend(parsed_units)
        context_units_by_part[str(part_name)] = [unit["unit_id"] for unit in parsed_units]

    unit_index_path = _relative_file(root, manifest.get("unit_index"), "unit_index", errors)
    block_index_path = _relative_file(root, manifest.get("block_index"), "block_index", errors)
    bilingual_pairs_path = _relative_file(root, manifest.get("bilingual_pairs"), "bilingual_pairs", errors)

    unit_ids: list[str] = []
    unit_rows: list[dict[str, Any]] = []
    unit_rows_by_id: dict[str, dict[str, Any]] = {}
    if unit_index_path:
        try:
            unit_rows = _load_jsonl(unit_index_path)
        except ValueError as exc:
            errors.append(str(exc))
            unit_rows = []
        for row in unit_rows:
            unit_id = str(row.get("unit_id") or "").strip()
            if not unit_id:
                errors.append("unit index row missing unit_id")
                continue
            unit_ids.append(unit_id)
            unit_rows_by_id[unit_id] = row
            schema_version = str(row.get("schema_version") or "")
            if is_locator_v22 and schema_version != "qa-review-context-unit.v2.2":
                errors.append(f"unit {unit_id} locator-v2.2 schema_version must be qa-review-context-unit.v2.2")
            if is_locator_v22 or schema_version == "qa-review-context-unit.v2.2":
                if not isinstance(row.get("review_domains"), list) or not row.get("review_domains"):
                    errors.append(f"unit {unit_id} v2.2 review_domains must be a non-empty list")
                if not isinstance(row.get("locator_safety"), dict):
                    errors.append(f"unit {unit_id} v2.2 locator_safety must be an object")
                if not isinstance(row.get("section_path"), list):
                    errors.append(f"unit {unit_id} v2.2 section_path must be a list")
        if len(unit_ids) != len(set(unit_ids)):
            errors.append("unit index contains duplicate unit_id values")
        if len(unit_rows) != int(manifest.get("unit_count") or -1):
            errors.append(f"unit_count mismatch: manifest={manifest.get('unit_count')} actual={len(unit_rows)}")

    block_unit_ids: list[str] = []
    block_rows: list[dict[str, Any]] = []
    if block_index_path:
        try:
            block_rows = _load_jsonl(block_index_path)
        except ValueError as exc:
            errors.append(str(exc))
            block_rows = []
        known_unit_ids = set(unit_ids)
        for row in block_rows:
            block_id = str(row.get("block_id") or "").strip()
            row_unit_ids = _block_unit_ids(row)
            block_unit_ids.extend(row_unit_ids)
            if row.get("unit_count") is not None:
                try:
                    expected_count = int(row.get("unit_count"))
                except (TypeError, ValueError):
                    errors.append(f"unit_count for block {block_id or row.get('unit_id')} must be an integer")
                else:
                    if expected_count != len(row_unit_ids):
                        errors.append(
                            f"unit_count mismatch for block {block_id or row.get('unit_id')}: "
                            f"declared={expected_count} actual={len(row_unit_ids)}"
                        )
            if _is_v21_block(row):
                if not block_id:
                    errors.append("v2.1 block row missing block_id")
                block_kind = str(row.get("block_kind") or "").strip()
                if block_kind not in {"body", "section", "table"}:
                    errors.append(f"v2.1 block {block_id or '?'} has invalid block_kind: {block_kind or '<empty>'}")
                if not isinstance(row.get("unit_ids"), list) or not row_unit_ids:
                    errors.append(f"v2.1 block {block_id or '?'} must contain non-empty unit_ids")
                if not isinstance(row.get("review_domains"), list):
                    errors.append(f"v2.1 block {block_id or '?'} review_domains must be a list")
            if known_unit_ids:
                for unit_id in row_unit_ids:
                    if unit_id not in known_unit_ids:
                        errors.append(f"block {row.get('block_id')} references unknown unit: {unit_id}")
                    elif block_id:
                        unit_block_id = str(unit_rows_by_id.get(unit_id, {}).get("block_id") or "").strip()
                        if unit_block_id and unit_block_id != block_id:
                            errors.append(
                                f"unit {unit_id} block_id mismatch: unit_index={unit_block_id} block_index={block_id}"
                            )
            context_part = str(row.get("context_part") or "").strip()
            if context_part and context_part in context_units_by_part and row_unit_ids != context_units_by_part[context_part]:
                errors.append(f"block index units do not match context part {context_part}")

    if bilingual_pairs_path:
        try:
            _load_jsonl(bilingual_pairs_path)
        except ValueError as exc:
            errors.append(str(exc))

    metrics_name = str(manifest.get("metrics") or "").strip()
    if is_locator_v22 and not metrics_name:
        errors.append("locator-v2.2 manifest must declare metrics")
    if metrics_name:
        metrics_path = root / metrics_name
        if not metrics_path.exists():
            errors.append(f"missing metrics file: {metrics_name}")
        else:
            try:
                metrics = _load_json(metrics_path)
            except json.JSONDecodeError as exc:
                errors.append(f"{metrics_name}: invalid JSON: {exc}")
            else:
                if not isinstance(metrics, dict):
                    errors.append(f"{metrics_name}: JSON must be an object")
                else:
                    required_metric_fields = {
                        "unit_count": int,
                        "block_count": int,
                        "part_count": int,
                        "domain_counts": dict,
                        "table_unit_count": int,
                        "body_unit_count": int,
                        "short_anchor_risk_count": int,
                    }
                    if is_locator_v22 and metrics.get("schema_version") != "qa-review-context-metrics.v2.2":
                        errors.append("metrics schema_version must be qa-review-context-metrics.v2.2")
                    for field_name, field_type in required_metric_fields.items():
                        if field_name not in metrics:
                            if is_locator_v22:
                                errors.append(f"metrics missing {field_name}")
                            continue
                        if field_type is int:
                            if not _is_int(metrics.get(field_name)):
                                errors.append(f"metrics {field_name} must be an integer")
                        elif not isinstance(metrics.get(field_name), field_type):
                            errors.append(f"metrics {field_name} must be an object")
                    if metrics.get("unit_count") != manifest.get("unit_count"):
                        errors.append("metrics unit_count must equal manifest unit_count")
                    if not isinstance(metrics.get("domain_counts"), dict):
                        errors.append("metrics domain_counts must be an object")
                    if is_locator_v22 and metrics.get("block_count") != len(block_rows):
                        errors.append("metrics block_count must equal block row count")
                    if is_locator_v22 and metrics.get("part_count") != len(context_parts):
                        errors.append("metrics part_count must equal manifest context_parts count")
                    if is_locator_v22:
                        expected_metrics = _metrics_from_units(unit_rows)
                        if metrics.get("domain_counts") != expected_metrics["domain_counts"]:
                            errors.append("metrics domain_counts must match unit review_domains")
                        if metrics.get("table_unit_count") != expected_metrics["table_unit_count"]:
                            errors.append("metrics table_unit_count must match unit rows")
                        if metrics.get("body_unit_count") != expected_metrics["body_unit_count"]:
                            errors.append("metrics body_unit_count must match unit rows")
                        if metrics.get("short_anchor_risk_count") != expected_metrics["short_anchor_risk_count"]:
                            errors.append("metrics short_anchor_risk_count must match unit rows")

    if manifest.get("truncated") is not False:
        errors.append("context package must not be truncated")

    if manifest.get("covered_unit_count") != manifest.get("unit_count"):
        errors.append("covered_unit_count must equal unit_count")

    actual_unit_ids = [unit["unit_id"] for unit in context_units]
    actual_unit_id_set = set(actual_unit_ids)
    if len(actual_unit_ids) != len(set(actual_unit_ids)):
        errors.append("context parts contain duplicate UNIT sections")
    if manifest.get("covered_unit_count") != len(actual_unit_ids):
        errors.append(f"covered_unit_count mismatch: manifest={manifest.get('covered_unit_count')} actual={len(actual_unit_ids)}")

    if unit_ids:
        unit_id_set = set(unit_ids)
        missing_units = [unit_id for unit_id in unit_ids if unit_id not in actual_unit_id_set]
        if missing_units:
            preview = ", ".join(missing_units[:10])
            errors.append(f"context coverage missing unit text for: {preview}")
        extra_units = [unit_id for unit_id in actual_unit_ids if unit_id not in unit_id_set]
        if extra_units:
            preview = ", ".join(extra_units[:10])
            errors.append(f"context contains units not present in unit index: {preview}")

    if unit_ids and actual_unit_ids and unit_ids != actual_unit_ids:
        errors.append("context unit order does not match unit index")
    if block_unit_ids and actual_unit_ids and block_unit_ids != actual_unit_ids:
        errors.append("block index units do not match context units")

    for unit in context_units:
        unit_id = unit["unit_id"]
        row = unit_rows_by_id.get(unit_id)
        if not row:
            continue
        expected_text = str(row.get("text") or "")
        actual_text = unit["text"]
        if actual_text != expected_text:
            errors.append(f"text mismatch for unit {unit_id}")
        expected_hash = str(row.get("source_hash") or "").strip()
        if expected_hash and expected_hash != _source_hash(expected_text):
            errors.append(f"source_hash mismatch for unit {unit_id}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate QA agent context package.")
    parser.add_argument("manifest", help="Path to agent_context_manifest.json")
    args = parser.parse_args()

    errors = validate_package(Path(args.manifest))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("agent context package OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

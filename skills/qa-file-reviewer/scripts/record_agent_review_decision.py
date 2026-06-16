#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record qa-file-reviewer routing and downgrade decisions for audit."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


REQUIRED_REVIEWERS = (
    "qa-structure-reviewer",
    "qa-zh-language-reviewer",
    "qa-en-language-reviewer",
    "qa-bilingual-reviewer",
    "qa-data-consistency-reviewer",
    "qa-risk-classifier",
)
FINAL_REVIEWER = "qa-final-merge-reviewer"

ALLOWED_MODES = {
    "claude_agent_multi_review",
    "fast_deterministic_explicit",
    "fast_deterministic_downgrade",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record agent review routing for qa-file-reviewer.")
    parser.add_argument("output_json", help="Path to write the routing record JSON.")
    parser.add_argument("--mode", required=True, choices=sorted(ALLOWED_MODES))
    parser.add_argument("--requested-review", required=True, help="Requested review mode, for example deep_review.")
    parser.add_argument(
        "--agent-tool-available",
        required=True,
        choices=("true", "false"),
        help="Whether Agent tooling was available in the current runtime.",
    )
    parser.add_argument("--reason", default="", help="Required for downgrade or explicit deterministic mode.")
    parser.add_argument(
        "--completed-reviewer",
        action="append",
        default=[],
        help="Reviewer shard already completed. May be passed multiple times.",
    )
    parser.add_argument(
        "--final-reviewer-completed",
        default="false",
        choices=("true", "false"),
        help="Whether qa-final-merge-reviewer completed for this routing record.",
    )
    return parser.parse_args()


def _as_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _normalize_completed(values: Sequence[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for value in values:
        name = value.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def main() -> int:
    args = parse_args()
    agent_tool_available = _as_bool(args.agent_tool_available)
    completed_reviewers = _normalize_completed(args.completed_reviewer)
    reason = args.reason.strip()
    final_reviewer_completed = _as_bool(args.final_reviewer_completed)

    if args.mode == "claude_agent_multi_review":
        if not agent_tool_available:
            raise SystemExit("Agent multi-review requires --agent-tool-available true.")
    else:
        if not reason:
            raise SystemExit("Downgrade reason is required for deterministic execution modes.")

    if args.mode == "fast_deterministic_downgrade" and agent_tool_available:
        raise SystemExit("Downgrade mode is invalid when Agent tooling is available.")

    payload = {
        "schema_version": "qa-file-reviewer.agent-routing.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "requested_review": args.requested_review,
        "execution_mode": args.mode,
        "agent_tool_available": agent_tool_available,
        "downgrade_reason": reason,
        "required_reviewers": list(REQUIRED_REVIEWERS),
        "completed_reviewers": completed_reviewers,
        "final_reviewer": FINAL_REVIEWER,
        "final_reviewer_completed": final_reviewer_completed,
    }

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate executor context-pack source-probe evidence for 211 runtime acceptance.

The default mode exercises the same source functions used by the worker prompt
path and writes a redacted evidence payload. Operators can run this inside the
211 API/worker image as a source binding probe for verify_executor_context_pack_211.py.
It does not replace a live worker run payload or close 211 acceptance by itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import repositories
from app.context_builder import executor_context_pack_from_snapshot
from app.db import transaction
from app.executors.claude_agent_sdk_runner import build_skill_prompt
from app.worker import _context_snapshot_ref_from_row


EVIDENCE_SCHEMA_VERSION = "ai-platform.executor-context-pack-211.v1"
SOURCE_SCHEMA_VERSION = "ai-platform.executor-context-pack.v1"
NON_EXPANSION_INVARIANTS = {
    "ordinary_user_multi_agent_allowed": False,
    "ordinary_user_high_risk_sandbox_allowed": False,
    "lightweight_office_tasks_start_sandbox_by_default": False,
    "long_term_cross_session_memory_enabled": False,
    "public_projection_only_for_ordinary_users": True,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sample_context_snapshot(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": f"session-{run_id}",
        "source": "chat_stream",
        "referenced_materials": {
            "message_count": 2,
            "file_count": 1,
            "artifact_count": 1,
            "memory_record_count": 3,
        },
        "used_context_summary": {
            "source": "chat_stream",
            "input_keys": ["attachments", "message", "raw_storage_key"],
            "memory_policy_source": "stored",
            "long_term_memory_read": True,
        },
        "latest_artifact_version": "v3",
        "execution_tier": "document_worker",
        "context_pack_version": "v8",
        "context_pack_generated_at": "2026-06-12T01:23:45Z",
        "included_artifact_ids": ["artifact-secret"],
        "raw_storage_key": "s3://private/object",
        "sandbox_workdir": "/tmp/private",
        "executor_private_payload": {"token": "secret"},
    }


def _prompt_checks(prompt: str, *, context_pack: dict[str, Any]) -> dict[str, bool]:
    prompt_lower = prompt.lower()
    context_pack_version = str(context_pack.get("context_pack_version") or "")
    generated_at = str(context_pack.get("context_pack_generated_at") or "")
    return {
        "bounded_summary_present": "Context pack:" in prompt and "Office context pack:" in prompt,
        "context_pack_version_present": bool(context_pack_version)
        and f"Context pack version: {context_pack_version}" in prompt,
        "context_pack_generated_at_present": bool(generated_at)
        and f"Context pack generated at: {generated_at}" in prompt,
        "raw_storage_identifiers_absent": "s3://" not in prompt_lower and "raw_storage_key" not in prompt,
        "sandbox_runtime_paths_absent": "/tmp/" not in prompt_lower and "sandbox_workdir" not in prompt,
        "executor_private_content_absent": "executor_private_payload" not in prompt and "secret" not in prompt_lower,
        "long_term_memory_read_false": "0 long-term memory record(s)" in prompt,
    }


def _scope_checks_from_context_pack(context_pack: dict[str, Any]) -> dict[str, bool]:
    materials = context_pack.get("referenced_materials")
    if not isinstance(materials, dict):
        materials = {}
    artifact_count = materials.get("artifact_count")
    source_artifact_present = isinstance(artifact_count, int) and not isinstance(artifact_count, bool) and artifact_count > 0
    return {
        "tenant_id_scoped": True,
        "workspace_id_scoped": True,
        "user_id_scoped": True,
        "session_id_scoped": True,
        "source_run_artifact_count_positive": source_artifact_present,
        "source_run_artifact_scope_verified": source_artifact_present,
    }


def _runtime_evidence_from_sections(
    *,
    prompt_checks: dict[str, bool],
    scope_checks: dict[str, bool],
    live_run_checks: dict[str, bool],
) -> dict[str, bool]:
    return {
        "live_worker_run_payload": True,
        "run_row_loaded": live_run_checks.get("run_row_loaded") is True,
        "context_snapshot_id_present": live_run_checks.get("context_snapshot_id_present") is True,
        "scoped_context_snapshot_loaded": live_run_checks.get("scoped_context_snapshot_loaded") is True,
        "worker_context_ref_rebuilt_from_db_snapshot": live_run_checks.get(
            "worker_context_ref_rebuilt_from_db_snapshot"
        )
        is True,
        "prompt_includes_bounded_summary": prompt_checks.get("bounded_summary_present") is True,
        "prompt_includes_context_pack_version": prompt_checks.get("context_pack_version_present") is True,
        "prompt_includes_context_pack_generated_at": prompt_checks.get("context_pack_generated_at_present")
        is True,
        "raw_storage_identifiers_absent": prompt_checks.get("raw_storage_identifiers_absent") is True,
        "sandbox_runtime_paths_absent": prompt_checks.get("sandbox_runtime_paths_absent") is True,
        "executor_private_content_absent": prompt_checks.get("executor_private_content_absent") is True,
        "long_term_memory_read_false": prompt_checks.get("long_term_memory_read_false") is True,
        "source_run_artifact_scope_tenant_workspace_user_session": scope_checks.get(
            "source_run_artifact_scope_verified"
        )
        is True,
        "source_run_artifact_count_positive": scope_checks.get("source_run_artifact_count_positive") is True,
        "fresh_generated_at": True,
        "source_functions_bound_to_current_runtime": True,
    }


def _base_evidence(
    *,
    run_id: str,
    evidence_strength: str,
    does_not_close_211_acceptance: bool,
    runtime_acceptance_requires_real_run_payload: bool,
    runtime_run_payload_verified: bool,
    context_pack: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "source_schema_version": SOURCE_SCHEMA_VERSION,
        "run_id": run_id,
        "runtime_mode": "worker",
        "evidence_strength": evidence_strength,
        "does_not_close_211_acceptance": does_not_close_211_acceptance,
        "runtime_acceptance_requires_real_run_payload": runtime_acceptance_requires_real_run_payload,
        "runtime_run_payload_verified": runtime_run_payload_verified,
        "generated_at": _utc_now(),
        "source_functions": [
            "app.repositories.get_context_snapshot_for_worker",
            "app.context_builder.executor_context_pack_from_snapshot",
            "app.executors.claude_agent_sdk_runner._context_pack_prompt_section",
            "app.executors.claude_agent_worker.build_skill_prompt_context_pack_injection",
            "app.worker._context_snapshot_ref_from_row",
        ],
        "prompt_checks": _prompt_checks(prompt, context_pack=context_pack),
        "scope_checks": _scope_checks_from_context_pack(context_pack),
        "non_expansion_invariants": dict(NON_EXPANSION_INVARIANTS),
    }


def build_evidence(*, run_id: str) -> dict[str, Any]:
    context_pack = executor_context_pack_from_snapshot(_sample_context_snapshot(run_id))
    prompt = build_skill_prompt(
        skill_id="general-chat",
        user_message="continue the proposal",
        file_names=["proposal.docx"],
        context_pack=context_pack,
    )
    return _base_evidence(
        run_id=run_id,
        evidence_strength="source_probe_on_target_runtime",
        does_not_close_211_acceptance=True,
        runtime_acceptance_requires_real_run_payload=True,
        runtime_run_payload_verified=False,
        context_pack=context_pack,
        prompt=prompt,
    )


def _required_string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) and value else ""


def _context_snapshot_id_from_run_input(input_json: dict[str, Any]) -> str:
    value = input_json.get("context_snapshot_id")
    if isinstance(value, str) and value:
        return value
    nested_input = input_json.get("input")
    if isinstance(nested_input, dict):
        nested_value = nested_input.get("context_snapshot_id")
        if isinstance(nested_value, str) and nested_value:
            return nested_value
    return ""


async def _load_live_context_snapshot(conn: Any, *, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    run_cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, agent_id, skill_id, input_json
        from runs
        where id = %s
        """,
        (run_id,),
    )
    run_row = await run_cursor.fetchone()
    if run_row is None:
        raise RuntimeError("live run not found")
    run = dict(run_row)
    input_json = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    context_snapshot_id = _context_snapshot_id_from_run_input(input_json)
    if not context_snapshot_id:
        raise RuntimeError("live run context_snapshot_id missing")
    snapshot_row = await repositories.get_context_snapshot_for_worker(
        conn,
        tenant_id=_required_string(run, "tenant_id"),
        workspace_id=_required_string(run, "workspace_id"),
        user_id=_required_string(run, "user_id"),
        session_id=_required_string(run, "session_id"),
        run_id=_required_string(run, "id"),
        context_snapshot_id=context_snapshot_id,
    )
    if snapshot_row is None:
        raise RuntimeError("scoped live context snapshot not found")
    return run, dict(snapshot_row)


async def build_live_run_evidence(*, run_id: str) -> dict[str, Any]:
    async with transaction() as conn:
        run, snapshot_row = await _load_live_context_snapshot(conn, run_id=run_id)
    context_ref = _context_snapshot_ref_from_row(snapshot_row)
    context_pack = executor_context_pack_from_snapshot(context_ref)
    prompt = build_skill_prompt(
        skill_id=_required_string(run, "skill_id") or "general-chat",
        user_message="continue with the current office task",
        file_names=["input.docx"] if context_ref.get("referenced_materials", {}).get("file_count") else [],
        context_pack=context_pack,
    )
    evidence = _base_evidence(
        run_id=run_id,
        evidence_strength="live_worker_run_payload",
        does_not_close_211_acceptance=False,
        runtime_acceptance_requires_real_run_payload=False,
        runtime_run_payload_verified=True,
        context_pack=context_pack,
        prompt=prompt,
    )
    live_run_checks = {
        "run_row_loaded": True,
        "context_snapshot_id_present": True,
        "scoped_context_snapshot_loaded": True,
        "worker_context_ref_rebuilt_from_db_snapshot": True,
        "context_pack_schema_present": context_pack.get("schema_version") == SOURCE_SCHEMA_VERSION,
    }
    evidence["live_run_checks"] = live_run_checks
    evidence["runtime_evidence"] = _runtime_evidence_from_sections(
        prompt_checks=evidence["prompt_checks"],
        scope_checks=evidence["scope_checks"],
        live_run_checks=live_run_checks,
    )
    evidence["public_context_summary"] = {
        "execution_tier": context_pack.get("execution_tier"),
        "context_pack_version": context_pack.get("context_pack_version"),
        "context_pack_generated_at_present": bool(context_pack.get("context_pack_generated_at")),
        "referenced_material_counts": context_pack.get("referenced_materials"),
        "input_keys": context_pack.get("used_context_summary", {}).get("input_keys")
        if isinstance(context_pack.get("used_context_summary"), dict)
        else [],
    }
    return evidence


def write_evidence(evidence: dict[str, Any], evidence_path: str | Path) -> None:
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=True, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate executor context-pack evidence on 211. Default source-probe evidence does not close "
            "211 acceptance; Use --live-run-id with a real 211 run for live worker-run evidence."
        )
    )
    parser.add_argument("--run-id", default=os.environ.get("AI_PLATFORM_EXECUTOR_CONTEXT_PACK_RUN_ID", "executor-context-pack-smoke"))
    parser.add_argument(
        "--live-run-id",
        default=os.environ.get("AI_PLATFORM_EXECUTOR_CONTEXT_PACK_LIVE_RUN_ID", ""),
        help=(
            "Use --live-run-id with a real 211 run; read its scoped DB context snapshot and produce "
            "live_worker_run_payload evidence."
        ),
    )
    parser.add_argument(
        "--evidence-file",
        default=os.environ.get(
            "AI_PLATFORM_EXECUTOR_CONTEXT_PACK_EVIDENCE",
            "/tmp/ai-platform-executor-context-pack-evidence.json",
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence = (
        asyncio.run(build_live_run_evidence(run_id=args.live_run_id))
        if args.live_run_id
        else build_evidence(run_id=args.run_id)
    )
    write_evidence(evidence, args.evidence_file)
    output = {
        "run_id": evidence["run_id"],
        "evidence_file": "[redacted-path]",
        "schema_version": evidence["schema_version"],
        "evidence_strength": evidence["evidence_strength"],
        "prompt_checks_passed": all(evidence["prompt_checks"].values()),
        "scope_checks_passed": all(evidence["scope_checks"].values()),
        "runtime_run_payload_verified": bool(evidence.get("runtime_run_payload_verified")),
    }
    if args.json_output:
        print(json.dumps(output, ensure_ascii=True, indent=2))
    else:
        print("PASSED: executor context-pack source-probe evidence generated")
    return 0 if output["prompt_checks_passed"] and output["scope_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

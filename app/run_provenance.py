import re

from app.auth import AuthPrincipal, is_ai_admin
from app.control_plane_contracts import (
    ARTIFACT_LINEAGE_ID_PREFIXES,
    HASH_LIKE_VALUE_PATTERN,
    artifact_lineage_contract,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import (
    CAPABILITY_BY_AGENT_ID,
    CAPABILITY_BY_SKILL_ID,
    PUBLIC_AGENT_ID_BY_CAPABILITY,
    capability_id_from_skill,
    public_agent_id_for_projection,
)
from app.run_projection import (
    artifact_card,
    executor_result_schema_version,
    normalize_run_status,
    normalize_step_status,
    progress_for_status,
    public_text_or_fallback,
    public_terminal_projection,
    run_contract_version,
    run_step_response,
    run_step_responses,
)
from app.validation import assert_safe_id

RUN_PROVENANCE_CONTRACT_VERSION = "ai-platform.run-provenance.v1"
RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION = "ai-platform.run-checkpoint-audit.v1"
RUN_CONTROL_PUBLIC_AGENT_IDS = set(PUBLIC_AGENT_ID_BY_CAPABILITY.values())
RUN_CONTROL_RAW_PROJECTION_TERMS = {
    *CAPABILITY_BY_SKILL_ID.keys(),
    *(set(CAPABILITY_BY_AGENT_ID.keys()) - RUN_CONTROL_PUBLIC_AGENT_IDS),
}


def _readiness_raw_projection_terms(run: dict[str, object]) -> set[str]:
    terms = {term.lower() for term in RUN_CONTROL_RAW_PROJECTION_TERMS if term}
    raw_skill_id = str(run.get("skill_id") or "")
    if raw_skill_id:
        terms.add(raw_skill_id.lower())
    raw_agent_id = str(run.get("agent_id") or "")
    public_agent_id = public_agent_id_for_projection(raw_agent_id, raw_skill_id)
    if raw_agent_id and raw_agent_id != public_agent_id:
        terms.add(raw_agent_id.lower())
    return terms


def _contains_raw_projection_term(text: str, raw_terms: set[str]) -> bool:
    normalized = text.lower()
    return any(term and term in normalized for term in raw_terms)


def _readiness_public_text(value: object, *, fallback: object = "", raw_terms: set[str]) -> str:
    text = public_text_or_fallback(value)
    if text and not _contains_raw_projection_term(text, raw_terms):
        return text
    fallback_text = public_text_or_fallback(fallback)
    if fallback_text and not _contains_raw_projection_term(fallback_text, raw_terms):
        return fallback_text
    return ""


def _contains_hash_like_fingerprint(text: str) -> bool:
    if HASH_LIKE_VALUE_PATTERN.fullmatch(text.strip()):
        return True
    return any(HASH_LIKE_VALUE_PATTERN.fullmatch(token) for token in re.split(r"[^A-Fa-f0-9]+", text))


def _fingerprint_safe_public_text(value: object, *, fallback: object = "", raw_terms: set[str]) -> str:
    text = _readiness_public_text(value, raw_terms=raw_terms)
    if text and not _contains_hash_like_fingerprint(text):
        return text
    fallback_text = _readiness_public_text(fallback, raw_terms=raw_terms)
    if fallback_text and not _contains_hash_like_fingerprint(fallback_text):
        return fallback_text
    return ""


readiness_raw_projection_terms = _readiness_raw_projection_terms
contains_raw_projection_term = _contains_raw_projection_term
readiness_public_text = _readiness_public_text


def _unique_sorted(values: list[object]) -> list[str]:
    return sorted({str(item) for item in values if item})


def _safe_provenance_graph_id(field_name: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    sanitized = sanitize_public_text(raw)
    if not sanitized or sanitized != raw:
        return None
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized):
        return None
    try:
        safe_id = assert_safe_id(sanitized, field_name)
    except ValueError:
        return None
    normalized = safe_id.lower()
    prefixes = ARTIFACT_LINEAGE_ID_PREFIXES.get(field_name, ())
    if not any(normalized == prefix or normalized.startswith(f"{prefix}-") or normalized.startswith(f"{prefix}_") for prefix in prefixes):
        return None
    return safe_id


safe_provenance_graph_id = _safe_provenance_graph_id


def _checkpoint_audit_safe_checkpoint_id(
    value: object,
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> str | None:
    checkpoint_id = _safe_provenance_graph_id("checkpoint_id", value)
    if checkpoint_id is None:
        return None
    if not is_ai_admin(principal) and _contains_raw_projection_term(checkpoint_id, raw_terms):
        return None
    return checkpoint_id


def _provenance_step_card(row: dict[str, object], principal: AuthPrincipal) -> dict[str, object]:
    card = run_step_response(row, principal=principal)
    raw_payload = card.get("payload")
    if not isinstance(raw_payload, dict):
        return card
    payload = dict(raw_payload)
    checkpoint_id = _safe_provenance_graph_id("checkpoint_id", payload.get("checkpoint_id"))
    subagent_id = _safe_provenance_graph_id("subagent_id", payload.get("subagent_id"))
    if checkpoint_id:
        payload["checkpoint_id"] = checkpoint_id
        payload["checkpoint_reused"] = bool(payload.get("checkpoint_reused"))
    else:
        payload.pop("checkpoint_id", None)
        payload.pop("checkpoint_reused", None)
    if subagent_id:
        payload["subagent_id"] = subagent_id
    else:
        payload.pop("subagent_id", None)
    card["payload"] = payload
    return card


def _add_provenance_edge(
    edges: list[dict[str, str]],
    seen_edges: set[tuple[str, str, str]],
    *,
    source_id: object,
    target_id: object,
    edge_kind: str,
) -> None:
    source = str(source_id) if source_id else ""
    target = str(target_id) if target_id else ""
    if not source or not target:
        return
    key = (source, target, edge_kind)
    if key in seen_edges:
        return
    seen_edges.add(key)
    edges.append({"source_id": source, "target_id": target, "edge_kind": edge_kind})


def _artifact_raw_lineage_value(row: dict[str, object], key: str) -> object:
    if key in row:
        return row.get(key)
    manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
    return manifest.get(key)


def _artifact_graph_id_from_row_or_lineage(
    *,
    field_name: str,
    row: dict[str, object],
    lineage: dict[str, object],
    unsafe_gap: str,
    missing_gap: str | None = None,
) -> tuple[str | None, list[str]]:
    raw_value = _artifact_raw_lineage_value(row, field_name)
    candidate = raw_value if raw_value is not None else lineage.get(field_name)
    if candidate is None:
        return None, [missing_gap] if missing_gap else []
    graph_id = _safe_provenance_graph_id(field_name, candidate)
    if graph_id is None:
        return None, [unsafe_gap]
    return graph_id, []


def _artifact_tree_source_step(
    *,
    row: dict[str, object],
    lineage: dict[str, object],
    step_by_id: dict[str, dict[str, object]],
) -> tuple[str | None, list[str]]:
    source_step_id, gaps = _artifact_graph_id_from_row_or_lineage(
        field_name="source_step_id",
        row=row,
        lineage=lineage,
        unsafe_gap="artifact_source_step_unsafe",
        missing_gap="artifact_source_step_missing",
    )
    if source_step_id and source_step_id not in step_by_id:
        gaps.append("producer_step_missing")
    return source_step_id, sorted(set(gaps))


def _artifact_tree_checkpoint(
    *,
    row: dict[str, object],
    lineage: dict[str, object],
) -> tuple[str | None, list[str]]:
    return _artifact_graph_id_from_row_or_lineage(
        field_name="checkpoint_id",
        row=row,
        lineage=lineage,
        unsafe_gap="artifact_checkpoint_unsafe",
    )


def _artifact_tree_subagent(
    *,
    row: dict[str, object],
    lineage: dict[str, object],
) -> tuple[str | None, list[str]]:
    return _artifact_graph_id_from_row_or_lineage(
        field_name="subagent_id",
        row=row,
        lineage=lineage,
        unsafe_gap="artifact_subagent_unsafe",
    )


def _artifact_tree_lineage(
    lineage: dict[str, object],
    *,
    source_step_id: str | None,
    checkpoint_id: str | None,
    subagent_id: str | None,
) -> dict[str, object]:
    projected = artifact_lineage_contract(lineage)
    projected.pop("source_run_id", None)
    projected.pop("source_step_id", None)
    projected.pop("checkpoint_id", None)
    projected.pop("subagent_id", None)
    if source_step_id:
        projected["source_step_id"] = source_step_id
    if checkpoint_id:
        projected["checkpoint_id"] = checkpoint_id
    if subagent_id:
        projected["subagent_id"] = subagent_id
    return projected


def _artifact_tree_parent(
    *,
    produced_by_step_id: str | None,
    checkpoint_id: object,
    subagent_id: object,
) -> tuple[str | None, str | None]:
    if produced_by_step_id:
        return produced_by_step_id, "step"
    if checkpoint_id:
        return str(checkpoint_id), "checkpoint"
    if subagent_id:
        return str(subagent_id), "subagent"
    return None, None


def run_playback_summary(run: dict[str, object], principal: AuthPrincipal) -> dict[str, object]:
    raw_skill_id = str(run["skill_id"])
    raw_agent_id = str(run["agent_id"])
    show_raw_skill = is_ai_admin(principal)
    terminal_projection = (
        public_terminal_projection(run.get("status"), run.get("error_code"))
        if not show_raw_skill
        else None
    )
    return {
        "run_id": str(run["id"]),
        "session_id": str(run["session_id"]),
        "agent_id": raw_agent_id if show_raw_skill else public_agent_id_for_projection(raw_agent_id, raw_skill_id),
        "skill_id": raw_skill_id if show_raw_skill else None,
        "capability_id": capability_id_from_skill(raw_skill_id, raw_agent_id),
        "trace_id": (
            str(run.get("trace_id") or standard_trace_id(str(run["id"])))
            if show_raw_skill
            else standard_trace_id(str(run["id"]))
        ),
        "contract_version": run_contract_version(run),
        "executor_schema_version": executor_result_schema_version(run) if show_raw_skill else None,
        "status": normalize_run_status(str(run["status"])),
        "progress": progress_for_status(str(run["status"])),
        "cancel_requested_at": run.get("cancel_requested_at"),
        "cancel_requested_by": run.get("cancel_requested_by"),
        "error_code": (
            sanitize_public_text(run.get("error_code"))
            if show_raw_skill
            else (
                terminal_projection["error_code"]
                if terminal_projection is not None
                else ("run_failed" if run.get("error_code") else None)
            )
        ),
        "error_message": (
            str(terminal_projection["message"])
            if terminal_projection is not None
            else sanitize_public_text(run.get("error_message"))
        ),
    }


def _ordinary_run_provenance_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    """Return server-owned artifact and step facts without executor lineage."""
    step_cards = run_step_responses(steps, principal=principal)
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    artifact_tree = [
        {
            "node_id": str(artifact["artifact_id"]),
            "node_kind": "artifact",
            "artifact_id": str(artifact["artifact_id"]),
            "artifact_type": artifact.get("artifact_type"),
            "label": artifact.get("label"),
            "produced_by_step_id": None,
            "source_step_id": None,
            "parent_id": None,
            "parent_kind": None,
            "children_ids": [],
            "producer_kind": None,
            "producer_role": None,
            "checkpoint_id": None,
            "subagent_id": None,
            "lineage": {},
            "gaps": [],
        }
        for artifact in artifact_cards
    ]
    return {
        "contract_version": RUN_PROVENANCE_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
        "steps": step_cards,
        "artifact_tree": artifact_tree,
        "checkpoints": [],
        "subagents": [],
        "graph": {
            "counts": {
                "steps": len(step_cards),
                "artifacts": len(artifact_cards),
                "checkpoints": 0,
                "subagents": 0,
            },
            "edges": [],
            "gaps": [],
        },
    }


def run_provenance_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    """Build the public run provenance graph from existing sanitized projections."""
    if not is_ai_admin(principal):
        return _ordinary_run_provenance_snapshot(run=run, steps=steps, artifacts=artifacts, principal=principal)
    step_cards = [_provenance_step_card(row, principal=principal) for row in steps]
    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    step_by_id = {str(item["step_id"]): item for item in step_cards}
    artifacts_by_checkpoint: dict[str, list[str]] = {}
    artifacts_by_subagent: dict[str, list[str]] = {}
    checkpoints: dict[str, dict[str, object]] = {}
    subagents: dict[str, dict[str, object]] = {}
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    artifact_tree: list[dict[str, object]] = []
    graph_gaps: list[dict[str, object]] = []
    for step in step_cards:
        raw_payload = step.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        step_id = str(step["step_id"])
        checkpoint_id = payload.get("checkpoint_id")
        subagent_id = payload.get("subagent_id")
        if checkpoint_id:
            checkpoint_key = str(checkpoint_id)
            checkpoint = checkpoints.setdefault(
                checkpoint_key,
                {"checkpoint_id": checkpoint_key, "step_ids": [], "artifact_ids": [], "reused": False},
            )
            checkpoint["step_ids"].append(step_id)
            checkpoint["reused"] = bool(checkpoint["reused"]) or bool(payload.get("checkpoint_reused"))
        if subagent_id:
            subagent_key = str(subagent_id)
            subagent = subagents.setdefault(
                subagent_key,
                {
                    "subagent_id": subagent_key,
                    "role": step.get("role"),
                    "step_ids": [],
                    "statuses": [],
                    "checkpoint_ids": [],
                    "artifact_ids": [],
                },
            )
            subagent["step_ids"].append(step_id)
            subagent["statuses"].append(step.get("status"))
            if checkpoint_id:
                subagent["checkpoint_ids"].append(str(checkpoint_id))
        if checkpoint_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=step_id,
                target_id=checkpoint_id,
                edge_kind="step_checkpoint",
            )
        if subagent_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=subagent_id,
                target_id=step_id,
                edge_kind="subagent_step",
            )

    for row, artifact in zip(artifacts, artifact_cards):
        raw_lineage = artifact.get("lineage")
        lineage = raw_lineage if isinstance(raw_lineage, dict) else {}
        source_step_id, source_gaps = _artifact_tree_source_step(row=row, lineage=lineage, step_by_id=step_by_id)
        checkpoint_id, checkpoint_gaps = _artifact_tree_checkpoint(row=row, lineage=lineage)
        subagent_id, subagent_gaps = _artifact_tree_subagent(row=row, lineage=lineage)
        gaps = sorted(set(source_gaps + checkpoint_gaps + subagent_gaps))
        public_lineage = _artifact_tree_lineage(
            lineage,
            source_step_id=source_step_id,
            checkpoint_id=checkpoint_id,
            subagent_id=subagent_id,
        )
        artifact_id = str(artifact["artifact_id"])
        if checkpoint_id:
            artifacts_by_checkpoint.setdefault(checkpoint_id, []).append(artifact_id)
        if subagent_id:
            artifacts_by_subagent.setdefault(subagent_id, []).append(artifact_id)
        produced_by_step_id = source_step_id if source_step_id in step_by_id else None
        parent_id, parent_kind = _artifact_tree_parent(
            produced_by_step_id=produced_by_step_id,
            checkpoint_id=checkpoint_id,
            subagent_id=subagent_id,
        )
        if produced_by_step_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=produced_by_step_id,
                target_id=artifact_id,
                edge_kind="produced_artifact",
            )
        if checkpoint_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=checkpoint_id,
                target_id=artifact_id,
                edge_kind="checkpoint_artifact",
            )
        if subagent_id:
            _add_provenance_edge(
                edges,
                seen_edges,
                source_id=subagent_id,
                target_id=artifact_id,
                edge_kind="subagent_artifact",
            )
        if gaps:
            graph_gaps.append({"node_id": artifact_id, "node_kind": "artifact", "gaps": gaps})
        artifact_tree.append(
            {
                "node_id": artifact_id,
                "node_kind": "artifact",
                "artifact_id": artifact_id,
                "artifact_type": artifact.get("artifact_type"),
                "label": artifact.get("label"),
                "produced_by_step_id": produced_by_step_id,
                "source_step_id": source_step_id,
                "parent_id": parent_id,
                "parent_kind": parent_kind,
                "children_ids": [],
                "producer_kind": public_lineage.get("producer_kind"),
                "producer_role": public_lineage.get("producer_role"),
                "checkpoint_id": checkpoint_id,
                "subagent_id": subagent_id,
                "lineage": public_lineage,
                "gaps": gaps,
            }
        )

    for checkpoint_id, artifact_ids in artifacts_by_checkpoint.items():
        checkpoint = checkpoints.setdefault(
            checkpoint_id,
            {"checkpoint_id": checkpoint_id, "step_ids": [], "artifact_ids": [], "reused": False},
        )
        checkpoint["artifact_ids"].extend(artifact_ids)
    for subagent_id, artifact_ids in artifacts_by_subagent.items():
        subagent = subagents.setdefault(
            subagent_id,
            {
                "subagent_id": subagent_id,
                "role": None,
                "step_ids": [],
                "statuses": [],
                "checkpoint_ids": [],
                "artifact_ids": [],
            },
        )
        subagent["artifact_ids"].extend(artifact_ids)

    checkpoint_items = [
        {
            "checkpoint_id": str(item["checkpoint_id"]),
            "step_ids": _unique_sorted(item["step_ids"]),
            "artifact_ids": _unique_sorted(item["artifact_ids"]),
            "reused": bool(item["reused"]),
        }
        for item in checkpoints.values()
    ]
    subagent_items = [
        {
            "subagent_id": str(item["subagent_id"]),
            "role": item.get("role"),
            "step_ids": _unique_sorted(item["step_ids"]),
            "statuses": _unique_sorted(item["statuses"]),
            "checkpoint_ids": _unique_sorted(item["checkpoint_ids"]),
            "artifact_ids": _unique_sorted(item["artifact_ids"]),
        }
        for item in subagents.values()
    ]
    return {
        "contract_version": RUN_PROVENANCE_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
        "steps": step_cards,
        "artifact_tree": artifact_tree,
        "checkpoints": sorted(checkpoint_items, key=lambda item: item["checkpoint_id"]),
        "subagents": sorted(subagent_items, key=lambda item: item["subagent_id"]),
        "graph": {
            "counts": {
                "steps": len(step_cards),
                "artifacts": len(artifact_cards),
                "checkpoints": len(checkpoint_items),
                "subagents": len(subagent_items),
            },
            "edges": edges,
            "gaps": graph_gaps,
        },
    }


def _checkpoint_audit_step_label(
    row: dict[str, object],
    principal: AuthPrincipal,
    *,
    raw_terms: set[str],
) -> str:
    public_step = run_step_response(row, principal=principal)
    step_id = str(public_step["step_id"])
    step_key = str(public_step["step_key"])
    if is_ai_admin(principal):
        return step_key
    return _fingerprint_safe_public_text(step_key, fallback=step_id, raw_terms=raw_terms) or step_id


def _checkpoint_audit_state(
    *,
    has_steps: bool,
    resume_reusable: bool,
    artifact_materialized: bool,
) -> str:
    if resume_reusable and artifact_materialized:
        return "materialized"
    if has_steps and not artifact_materialized:
        return "step_only" if resume_reusable else "incomplete"
    if artifact_materialized and not has_steps:
        return "artifact_only"
    return "incomplete"


def run_checkpoint_audit_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    artifacts: list[dict[str, object]],
    principal: AuthPrincipal,
) -> dict[str, object]:
    """Return read-only checkpoint reusable-output and artifact materialization state."""
    if not is_ai_admin(principal):
        # Checkpoint identifiers and artifact lineage originate with executor
        # payloads.  Ordinary users receive only the canonical run summary and
        # count-only state; detailed checkpoint correlation is private/admin
        # diagnostics.
        public_steps = run_step_responses(steps, principal=principal)
        reused = sum(
            1
            for step in public_steps
            if isinstance(step.get("payload"), dict)
            and bool(step["payload"].get("checkpoint_reused"))
        )
        reuse_pending = sum(
            1
            for step in public_steps
            if isinstance(step.get("payload"), dict)
            and bool(step["payload"].get("checkpoint_reuse_pending"))
        )
        return {
            "contract_version": RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION,
            "run": run_playback_summary(run, principal),
            "counts": {
                "checkpoints": 0,
                "resume_reusable": reused,
                "artifact_materialized": 0,
                "step_only": 0,
                "artifact_only": 0,
                "incomplete": 0,
                "gaps": 0,
                "uncheckpointed_reusable_steps": reuse_pending,
            },
            "checkpoints": [],
            "uncheckpointed_reusable_steps": [],
        }
    raw_terms = _readiness_raw_projection_terms(run)
    checkpoints: dict[str, dict[str, object]] = {}
    step_ids = {str(row["id"]) for row in steps}
    step_checkpoint_ids: dict[str, str] = {}
    uncheckpointed: list[dict[str, object]] = []

    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        status = normalize_step_status(row.get("status"))
        output_available = status == "succeeded" and payload.get("output") is not None
        checkpoint_id = _checkpoint_audit_safe_checkpoint_id(payload.get("checkpoint_id"), principal, raw_terms=raw_terms)
        if checkpoint_id:
            step_checkpoint_ids[str(row["id"])] = checkpoint_id
            item = checkpoints.setdefault(
                checkpoint_id,
                {
                    "checkpoint_id": checkpoint_id,
                    "step_ids": [],
                    "artifact_ids": [],
                    "resume_reusable": False,
                    "artifact_materialized": False,
                    "reuse_pending": 0,
                    "reused": 0,
                    "gaps": set(),
                },
            )
            item["step_ids"].append(str(row["id"]))
            item["resume_reusable"] = bool(item["resume_reusable"]) or output_available
            item["reuse_pending"] = int(item["reuse_pending"]) + (1 if payload.get("checkpoint_reuse_pending") else 0)
            item["reused"] = int(item["reused"]) + (1 if payload.get("checkpoint_reused") else 0)
        elif output_available:
            uncheckpointed.append(
                {
                    "step_id": str(row["id"]),
                    "step_key": _checkpoint_audit_step_label(row, principal, raw_terms=raw_terms),
                    "status": status,
                    "reason": "missing_checkpoint_id",
                }
            )

    artifact_cards = [artifact_card(row, principal=principal) for row in artifacts]
    for row, artifact in zip(artifacts, artifact_cards):
        lineage = artifact.get("lineage") if isinstance(artifact.get("lineage"), dict) else {}
        checkpoint_id = _checkpoint_audit_safe_checkpoint_id(lineage.get("checkpoint_id"), principal, raw_terms=raw_terms)
        if not checkpoint_id:
            continue
        item = checkpoints.setdefault(
            checkpoint_id,
            {
                "checkpoint_id": checkpoint_id,
                "step_ids": [],
                "artifact_ids": [],
                "resume_reusable": False,
                "artifact_materialized": False,
                "reuse_pending": 0,
                "reused": 0,
                "gaps": set(),
            },
        )
        item["artifact_ids"].append(str(artifact["artifact_id"]))
        manifest = row.get("manifest_json") if isinstance(row.get("manifest_json"), dict) else {}
        raw_source_step_id = manifest.get("source_step_id") if isinstance(manifest, dict) else None
        source_step_id = _safe_provenance_graph_id("source_step_id", raw_source_step_id)
        source_step_checkpoint_id = step_checkpoint_ids.get(str(source_step_id)) if source_step_id else None
        if raw_source_step_id is None:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("artifact_source_step_missing")
            item["gaps"] = gaps
        elif source_step_id is None:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("artifact_source_step_unsafe")
            item["gaps"] = gaps
        elif str(source_step_id) not in step_ids:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("producer_step_missing")
            item["gaps"] = gaps
            if not item["step_ids"]:
                item["artifact_materialized"] = True
        elif source_step_checkpoint_id != checkpoint_id:
            gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
            gaps.add("producer_checkpoint_mismatch")
            item["gaps"] = gaps
        else:
            item["artifact_materialized"] = True

    checkpoint_items = []
    for item in checkpoints.values():
        step_ids_for_checkpoint = _unique_sorted(item["step_ids"] if isinstance(item["step_ids"], list) else [])
        artifact_ids = _unique_sorted(item["artifact_ids"] if isinstance(item["artifact_ids"], list) else [])
        resume_reusable = bool(item["resume_reusable"])
        artifact_materialized = bool(item["artifact_materialized"])
        state = _checkpoint_audit_state(
            has_steps=bool(step_ids_for_checkpoint),
            resume_reusable=resume_reusable,
            artifact_materialized=artifact_materialized,
        )
        gaps = item["gaps"] if isinstance(item["gaps"], set) else set()
        gaps = set(gaps)
        if bool(step_ids_for_checkpoint) and not resume_reusable:
            gaps.add("no_reusable_output")
        if state == "step_only" and not artifact_ids:
            gaps.add("no_artifact_lineage")
        if state == "artifact_only" and not gaps:
            gaps.add("producer_step_missing")
        checkpoint_items.append(
            {
                "checkpoint_id": str(item["checkpoint_id"]),
                "audit_state": state,
                "resume_reusable": resume_reusable,
                "artifact_materialized": artifact_materialized,
                "step_ids": step_ids_for_checkpoint,
                "artifact_ids": artifact_ids,
                "reuse": {
                    "pending": int(item["reuse_pending"]),
                    "reused": int(item["reused"]),
                },
                "gaps": sorted(gaps),
            }
        )

    checkpoint_items = sorted(checkpoint_items, key=lambda entry: str(entry["checkpoint_id"]))
    counts = {
        "checkpoints": len(checkpoint_items),
        "resume_reusable": sum(1 for item in checkpoint_items if item["resume_reusable"]),
        "artifact_materialized": sum(1 for item in checkpoint_items if item["artifact_materialized"]),
        "step_only": sum(1 for item in checkpoint_items if item["audit_state"] == "step_only"),
        "artifact_only": sum(1 for item in checkpoint_items if item["audit_state"] == "artifact_only"),
        "incomplete": sum(1 for item in checkpoint_items if item["audit_state"] == "incomplete"),
        "gaps": sum(len(item["gaps"]) for item in checkpoint_items) + len(uncheckpointed),
        "uncheckpointed_reusable_steps": len(uncheckpointed),
    }
    run_summary = run_playback_summary(run, principal)
    if not is_ai_admin(principal):
        raw_error_message = run_summary.get("error_message")
        error_fallback = (
            "run_failed"
            if raw_error_message and normalize_run_status(str(run["status"])) == "failed"
            else ""
        )
        run_summary["error_message"] = _readiness_public_text(
            raw_error_message,
            fallback=error_fallback,
            raw_terms=raw_terms,
        )
    return {
        "contract_version": RUN_CHECKPOINT_AUDIT_CONTRACT_VERSION,
        "run": run_summary,
        "counts": counts,
        "checkpoints": checkpoint_items,
        "uncheckpointed_reusable_steps": uncheckpointed,
    }

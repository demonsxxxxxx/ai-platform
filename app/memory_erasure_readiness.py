from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ai-platform.memory-erasure-readiness.v1"


_EVIDENCE_MARKERS = [
    {
        "name": "ordinary_user_delete_route",
        "path": "app/routes/context.py",
        "markers": [
            '@router.delete("/memory/records/{record_id}")',
            "session_id = _safe_query_id(session_id, \"session_id\") if session_id else None",
            "row = await repositories.delete_memory_record(",
            'action="memory.record.deleted"',
            "return {\"memory_record\": _memory_delete_response(row)}",
        ],
    },
    {
        "name": "admin_delete_route",
        "path": "app/routes/context.py",
        "markers": [
            '@router.delete("/admin/memory/records/{record_id}")',
            "row = await repositories.admin_delete_memory_record(",
            'action="admin.memory.record.deleted"',
            "return {\"memory_record\": _memory_delete_response(row)}",
        ],
    },
    {
        "name": "admin_retention_cleanup_route",
        "path": "app/routes/context.py",
        "markers": [
            '@router.post("/admin/memory/retention/cleanup")',
            "rows = await repositories.cleanup_expired_memory_records(",
            'action="admin.memory.retention.cleanup"',
            '"deleted_count": len(rows)',
            '"memory_record_ids": [str(row.get("id")) for row in rows]',
        ],
    },
    {
        "name": "worker_retention_cleanup",
        "path": "app/worker_main.py",
        "markers": [
            "async def cleanup_expired_memory_records_for_worker(",
            "rows = await repositories.cleanup_expired_memory_records_across_scopes(",
            'action="worker.memory.retention.cleanup"',
            '"deleted_count": len(scope_rows)',
            '"memory_record_ids": [str(row.get("id")) for row in scope_rows]',
            '"source": "worker"',
        ],
    },
    {
        "name": "repository_soft_delete_without_content_returning",
        "path": "tests/test_repositories.py",
        "markers": [
            "test_delete_memory_record_soft_deletes_with_user_workspace_session_scope",
            "test_admin_delete_memory_record_soft_deletes_with_tenant_workspace_scope",
            'assert "content" not in sql',
            'assert "metadata_json" not in sql',
        ],
    },
    {
        "name": "route_delete_tests",
        "path": "tests/test_context_routes.py",
        "markers": [
            "test_delete_memory_record_soft_deletes_and_writes_audit",
            "test_admin_delete_memory_record_soft_deletes_same_tenant_record_and_writes_audit",
            "test_admin_cleanup_expired_memory_records_soft_deletes_and_audits_without_content",
            'assert "client-secret" not in str(calls)',
        ],
    },
    {
        "name": "worker_cleanup_tests",
        "path": "tests/test_worker_main.py",
        "markers": [
            "test_run_once_cleans_expired_memory_records_across_tenant_workspaces",
            "test_run_once_cleans_expired_memory_records_when_due",
            "test_run_once_does_not_audit_memory_cleanup_when_no_records_deleted",
            'assert [audit["action"] for audit in audit_calls] == ["worker.memory.retention.cleanup"] * 2',
        ],
    },
]


def _read_text(repo_root: Path, relative_path: str) -> str:
    path = repo_root / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _marker_evidence(repo_root: Path) -> list[dict[str, object]]:
    evidence = []
    for item in _EVIDENCE_MARKERS:
        text = _read_text(repo_root, str(item["path"]))
        missing = [marker for marker in item["markers"] if marker not in text]
        evidence.append(
            {
                "name": item["name"],
                "source": item["path"],
                "status": "present" if not missing else "missing",
                "missing_markers": missing,
            }
        )
    return evidence


def build_memory_erasure_readiness(repo_root: Path | None = None) -> dict[str, Any]:
    """Build a secret-safe G6 memory delete/retention erasure evidence snapshot."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    evidence = _marker_evidence(root)
    missing = [item["name"] for item in evidence if item["status"] != "present"]
    open_gaps = [
        "memory_export_erasure_evidence",
        "bounded_context_pack_product_contract_for_office_workflows",
        "memory_redaction_policy_admin_preview_and_audit",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked" if missing else "partial_blocked",
        "admin_runtime_projection": "/api/ai/admin/runtime/overview",
        "ordinary_user_policy": "session_scoped_delete_only",
        "implemented_controls": [
            "ordinary_user_session_scoped_soft_delete",
            "admin_same_tenant_soft_delete",
            "admin_retention_cleanup_soft_delete",
            "worker_retention_cleanup_across_scopes",
            "delete_and_cleanup_projection_without_content_or_metadata",
            "delete_and_cleanup_audit_payload_allowlist",
        ],
        "evidence_markers": evidence,
        "missing_evidence_markers": missing,
        "open_gaps": open_gaps,
        "evidence_policy": "delete_retention_tests_docs_and_211_smoke_required_before_memory_governance_closure",
    }


def render_memory_erasure_readiness_markdown(readiness: dict[str, Any]) -> str:
    """Render the memory erasure readiness snapshot as operator-readable Markdown."""
    controls = "\n".join(f"- {item}" for item in readiness["implemented_controls"])
    gaps = "\n".join(f"- {item}" for item in readiness["open_gaps"])
    markers = "\n".join(
        f"| `{item['name']}` | `{item['source']}` | `{item['status']}` |"
        for item in readiness["evidence_markers"]
    )
    missing = "\n".join(f"- {item}" for item in readiness["missing_evidence_markers"]) or "- none"
    return (
        "# ai-platform Memory Erasure Readiness\n\n"
        f"Schema: `{readiness['schema_version']}`\n\n"
        f"Status: `{readiness['status']}`\n\n"
        f"Admin Runtime projection: `{readiness['admin_runtime_projection']}`\n\n"
        "## Open Gaps\n\n"
        f"{gaps}\n\n"
        "## Implemented Controls\n\n"
        f"{controls}\n\n"
        "## Evidence Markers\n\n"
        "| Marker | Source | Status |\n"
        "| --- | --- | --- |\n"
        f"{markers}\n\n"
        "## Missing Evidence Markers\n\n"
        f"{missing}\n\n"
        "## Evidence Policy\n\n"
        f"{readiness['evidence_policy']}\n"
    )

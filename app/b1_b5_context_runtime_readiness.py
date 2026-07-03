from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

from app.control_plane_contracts import sanitize_public_payload
from app.context_manifest import ContextPlanner
from app.context_retrieval import ContextRetrieval, ContextRetrievalDenied, InMemoryContextRetrievalRepository
from app.executors.claude_agent_sdk_runner import ScopedContextRetrievalIdentity, build_skill_prompt, run_claude_agent_sdk
from app.session_continuity import InMemorySessionContinuityStore, SessionContinuity


SCHEMA_VERSION = "ai-platform.b1-b5-context-runtime-readiness.v1"
TARGET = "local_b1_b5_context_runtime"

REQUIRED_CHECKS = (
    "chat_prompt_uses_bounded_context_manifest",
    "document_prompt_uses_bounded_context_manifest",
    "large_file_requires_scoped_retrieval",
    "sdk_runner_wires_scoped_retrieval_tools",
    "stage_context_file_byte_cap_enforced",
    "public_projection_redacts_private_context_material",
    "session_continuity_persistence_design_recorded",
)

PRIVATE_MARKERS = (
    "storage_key",
    "raw_storage_key",
    "private_payload",
    "runtime_private_payload",
    "executor_private_payload",
    "sandbox_workdir",
    "tenants/tenant-a/private",
    "private/source",
    "c:\\users\\",
    "/home/",
)


def build_b1_b5_context_runtime_readiness(
    *,
    repo_root: Path | None = None,
    prompt_probe_private_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a local B1/B5 bounded-context runtime readiness verifier result."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    chat_probe = _prompt_probe(
        skill_id="general-chat",
        user_message="continue this chat",
        file_rows=[],
        private_payload=prompt_probe_private_payload,
    )
    document_probe = _prompt_probe(
        skill_id="qa-file-reviewer",
        user_message="review this document",
        file_rows=[
            {
                "id": "file-large",
                "original_name": "large-source.txt",
                "content_type": "text/plain",
                "size_bytes": 2_000_000,
                "text_preview": "large file preview should not be fully inlined",
            }
        ],
        private_payload=prompt_probe_private_payload,
    )
    sdk_probe = _run_async(_sdk_retrieval_probe())
    stage_probe = _run_async(_stage_byte_cap_probe())
    session_probe = _run_async(_session_continuity_probe())
    design_probe = _session_continuity_design_probe(root)
    sdk_evidence = _public_evidence(sdk_probe)
    stage_evidence = _public_evidence(stage_probe)
    session_evidence = _public_evidence(session_probe)
    design_evidence = _public_evidence(design_probe)
    redaction_payload = {
        "chat_prompt": chat_probe["prompt"],
        "document_prompt": document_probe["prompt"],
        "sdk_probe": sdk_evidence,
        "stage_probe": stage_evidence,
        "session_probe": session_evidence,
    }
    private_probe_present = (
        chat_probe["input_private_payload_present"] is True
        or document_probe["input_private_payload_present"] is True
    )
    redaction_ok = not private_probe_present and not _contains_private_marker(redaction_payload)
    checks = {
        "chat_prompt_uses_bounded_context_manifest": _check(
            chat_probe["bounded_manifest_prompt"] and chat_probe["retrieval_instruction_present"],
            {
                "manifest_ref_count": chat_probe["manifest_ref_count"],
                "retrieval_instruction_present": chat_probe["retrieval_instruction_present"],
            },
        ),
        "document_prompt_uses_bounded_context_manifest": _check(
            document_probe["bounded_manifest_prompt"] and document_probe["retrieval_instruction_present"],
            {
                "manifest_ref_count": document_probe["manifest_ref_count"],
                "retrieval_instruction_present": document_probe["retrieval_instruction_present"],
            },
        ),
        "large_file_requires_scoped_retrieval": _check(
            bool(document_probe["large_file_requires_retrieval"])
            and not bool(document_probe["large_file_inline_preview_present"]),
            {
                "large_file_requires_retrieval": document_probe["large_file_requires_retrieval"],
                "inline_preview_present": document_probe["large_file_inline_preview_present"],
            },
        ),
        "sdk_runner_wires_scoped_retrieval_tools": _check(
            sdk_probe["retrieval_tools_wired"] is True
            and sdk_probe["allowed_tools_include_retrieval"] is True
            and sdk_probe["stage_tool_redacted"] is True
            and sdk_probe["stage_tool_wrote_workspace_file"] is True,
            sdk_evidence,
        ),
        "stage_context_file_byte_cap_enforced": _check(
            stage_probe["oversize_denied"] is True and stage_probe["workspace_write_absent"] is True,
            stage_evidence,
        ),
        "public_projection_redacts_private_context_material": _check(
            redaction_ok,
            {
                "private_markers_present": _contains_private_marker(redaction_payload),
                "private_input_rejected": not private_probe_present,
            },
        ),
        "session_continuity_persistence_design_recorded": _check(
            session_probe["resume_key_stable"] is True
            and session_probe["fork_isolated"] is True
            and design_probe["recorded"] is True,
            {**session_evidence, **design_evidence},
        ),
    }
    ok = all(check["passed"] is True for check in checks.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "status": "local_runtime_verifier_ready" if ok else "blocked_runtime_contract",
        "status_label": "local partial",
        "target": TARGET,
        "source": {
            "repo_root_name": root.name,
            "does_not_touch_211": True,
            "runtime_subject": "local_source_only",
        },
        "checks": checks,
        "runtime_acceptance": {
            "machine_readable": True,
            "verifier_script": "tools/verify_b1_b5_context_runtime.py",
            "does_not_close_b1_or_b5_gate": True,
            "does_not_mark_211_verified": True,
        },
        "non_expansion_invariants": {
            "does_not_touch_211": True,
            "does_not_close_b1_or_b5_gate": True,
            "long_term_cross_session_memory_enabled": False,
            "public_projection_only_for_ordinary_users": True,
        },
    }


def render_b1_b5_context_runtime_readiness_markdown(readiness: dict[str, Any]) -> str:
    checks = readiness.get("checks") if isinstance(readiness.get("checks"), dict) else {}
    check_lines = "\n".join(
        f"- `{name}`: `{str(payload.get('passed') is True).lower()}`"
        for name, payload in checks.items()
        if isinstance(payload, dict)
    )
    return (
        "# B1/B5 Context Runtime Readiness\n\n"
        f"Schema: `{readiness.get('schema_version')}`\n\n"
        f"Status: `{readiness.get('status')}`\n\n"
        f"Status label: `{readiness.get('status_label')}`\n\n"
        f"Target: `{readiness.get('target')}`\n\n"
        "## Checks\n\n"
        f"{check_lines or '- none'}\n\n"
        "## Boundary\n\n"
        "This verifier is local-source runtime evidence only. It does not touch 211, "
        "does not close B1/B5 gates, and does not mark PR #307 as 211 verified.\n"
    )


def _prompt_probe(
    *,
    skill_id: str,
    user_message: str,
    file_rows: list[dict[str, Any]],
    private_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    planner = ContextPlanner(max_inline_file_bytes=1024, max_inline_file_preview_chars=128)
    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id=f"run-{skill_id}",
        agent_id="general-agent",
        skill_id=skill_id,
        current_message=user_message,
        recent_messages=[
            {
                "id": "msg-a",
                "run_id": "run-chat",
                "role": "user",
                "content": "short bounded chat message",
            }
        ],
        files=file_rows,
        artifacts=[],
        memory_records=[],
    )
    if private_payload:
        manifest["private_payload"] = private_payload
    context_pack = planner.executor_context_pack(manifest)
    prompt = build_skill_prompt(
        skill_id=skill_id,
        user_message=user_message,
        file_names=[str(row.get("original_name") or row.get("id") or "") for row in file_rows],
        context_pack=context_pack,
    )
    files = context_pack.get("context_manifest", {}).get("files", [])
    large_file = files[0] if files else {}
    return {
        "prompt": prompt,
        "input_private_payload_present": private_payload is not None,
        "manifest_ref_count": context_pack.get("referenced_materials"),
        "bounded_manifest_prompt": "Context manifest:" in prompt and "Context manifest refs:" in prompt,
        "retrieval_instruction_present": "Use context retrieval tools" in prompt,
        "large_file_requires_retrieval": large_file.get("requires_retrieval") is True,
        "large_file_inline_preview_present": bool(large_file.get("inline_preview")),
    }


async def _sdk_retrieval_probe() -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content: list[Any]) -> None:
            self.content = content

    class ResultMessage:
        session_id = "sdk-session-returned"
        usage: dict[str, Any] = {}
        model_usage: dict[str, Any] = {}
        result = "ok"
        is_error = False
        errors: list[Any] = []
        stop_reason = None

    class ClaudeAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    class SdkTool:
        def __init__(self, name: str, handler: Any) -> None:
            self.name = name
            self.handler = handler

    def tool(name: str, description: str, input_schema: dict[str, Any], annotations: Any = None):
        def decorator(handler: Any) -> SdkTool:
            return SdkTool(name, handler)

        return decorator

    def create_sdk_mcp_server(name: str, version: str = "1.0.0", tools: list[Any] | None = None) -> dict[str, Any]:
        return {"name": name, "version": version, "tools": tools or []}

    async def query(prompt: Any, options: Any):
        yield AssistantMessage([TextBlock("ok")])
        yield ResultMessage()

    settings = types.SimpleNamespace(
        claude_agent_sdk_enabled=True,
        anthropic_base_url="",
        anthropic_auth_token="",
        anthropic_model="",
        openai_api_key="",
        claude_agent_model="deepseek-v4-flash",
        claude_agent_sdk_skills="",
        claude_agent_sdk_timeout_seconds=5,
        claude_agent_sdk_max_turns=12,
        claude_agent_permission_mode="dontAsk",
        claude_agent_allowed_tools="Read,Glob,LS",
        claude_agent_disallowed_tools="",
    )
    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        create_sdk_mcp_server=create_sdk_mcp_server,
        query=query,
        tool=tool,
    )
    original_sdk = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = fake_sdk
    import app.executors.claude_agent_sdk_runner as sdk_runner

    original_get_settings = sdk_runner.get_settings
    sdk_runner.get_settings = lambda: settings
    with tempfile.TemporaryDirectory(prefix="ai-platform-b1-b5-sdk-") as workspace:
        tmp_root = Path(workspace)
        retrieval = ContextRetrieval(
            InMemoryContextRetrievalRepository(
                files=[
                    {
                        "tenant_id": "tenant-a",
                        "workspace_id": "workspace-a",
                        "user_id": "user-a",
                        "session_id": "session-a",
                        "run_id": "run-a",
                        "file_id": "file-a",
                        "original_name": "source.txt",
                        "content": "workspace staged content SENTINEL_RAW_CONTEXT_BODY_DO_NOT_LEAK",
                        "private_payload": {
                            "storage_key": "tenants/tenant-a/private/source.txt",
                            "raw_storage_key": "tenants/tenant-a/private/source.txt",
                            "sandbox_workdir": "C:\\Users\\agent\\private\\workspace",
                        },
                    }
                ]
            )
        )
        try:
            result = await run_claude_agent_sdk(
                prompt="hello",
                cwd=tmp_root,
                skill_id="general-chat",
                context_retrieval=retrieval,
                context_retrieval_identity=ScopedContextRetrievalIdentity(
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id="run-a",
                    agent_id="general-agent",
                ),
            )
            server = captured.get("mcp_servers", {}).get("ai-platform-context")
            tools = list(server.get("tools") or []) if isinstance(server, dict) else []
            stage_tool = next(tool for tool in tools if getattr(tool, "name", "") == "stage_context_file_to_workspace")
            stage_result = await stage_tool.handler({"file_id": "file-a"})
            stage_text = json.dumps(stage_result, ensure_ascii=False)
            return {
                "sdk_used": result.used_sdk,
                "retrieval_tools_wired": [getattr(tool, "name", "") for tool in tools]
                == [
                    "read_session_messages",
                    "read_context_file",
                    "read_run_artifact",
                    "stage_context_file_to_workspace",
                    "search_memory",
                ],
                "allowed_tools_include_retrieval": "stage_context_file_to_workspace" in captured.get("allowed_tools", []),
                "stage_tool_redacted": (
                    "storage_key" not in stage_text
                    and "workspace staged content" not in stage_text
                    and "SENTINEL_RAW_CONTEXT_BODY_DO_NOT_LEAK" not in stage_text
                ),
                "stage_tool_wrote_workspace_file": (tmp_root / "context" / "file-a" / "source.txt").exists(),
                "private_material_seeded": True,
                "private_payload": {
                    "storage_key": "tenants/tenant-a/private/source.txt",
                    "raw_storage_key": "tenants/tenant-a/private/source.txt",
                    "sandbox_workdir": "C:\\Users\\agent\\private\\workspace",
                },
            }
        finally:
            sdk_runner.get_settings = original_get_settings
            if original_sdk is None:
                sys.modules.pop("claude_agent_sdk", None)
            else:
                sys.modules["claude_agent_sdk"] = original_sdk
    return {
        "sdk_used": False,
        "retrieval_tools_wired": False,
        "allowed_tools_include_retrieval": False,
        "stage_tool_redacted": False,
        "stage_tool_wrote_workspace_file": False,
        "private_material_seeded": False,
    }


async def _stage_byte_cap_probe() -> dict[str, Any]:
    retrieval = ContextRetrieval(
        InMemoryContextRetrievalRepository(
            files=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "file_id": "file-large",
                    "original_name": "large.txt",
                    "content": "0123456789abcdef",
                }
            ]
        )
    )
    with tempfile.TemporaryDirectory(prefix="ai-platform-b1-b5-stage-") as workspace:
        tmp_root = Path(workspace)
        try:
            await retrieval.stage_context_file_to_workspace(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id="session-a",
                run_id="run-a",
                file_id="file-large",
                workspace_root=str(tmp_root),
                max_bytes=8,
            )
            denied = False
        except ContextRetrievalDenied as exc:
            denied = str(exc) == "context_file_too_large"
        return {
            "oversize_denied": denied,
            "workspace_write_absent": not (tmp_root / "context").exists(),
        }


async def _session_continuity_probe() -> dict[str, Any]:
    continuity = SessionContinuity(InMemorySessionContinuityStore())
    base = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="deepseek-v4-flash",
    )
    again = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="deepseek-v4-flash",
    )
    fork = await continuity.resolve(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
        skill_id="general-chat",
        model_key="deepseek-v4-flash",
        fork_reason="parallel_exploration",
    )
    return {
        "resume_key_stable": base.sdk_session_id == again.sdk_session_id,
        "fork_isolated": fork.sdk_session_id != base.sdk_session_id and fork.lock_key != base.lock_key,
        "in_process_store_only": True,
    }


def _session_continuity_design_probe(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "docs" / "operations" / "b1-b5-context-runtime-follow-up.md"
    if not path.exists():
        return {"recorded": False, "path": "docs/operations/b1-b5-context-runtime-follow-up.md"}
    text = path.read_text(encoding="utf-8").lower()
    required_terms = (
        "sdk session resume key",
        "fork isolation",
        "multi-worker lock",
        "restart recovery",
        "db/redis",
    )
    return {
        "recorded": all(term in text for term in required_terms),
        "path": "docs/operations/b1-b5-context-runtime-follow-up.md",
        "required_terms_present": {
            term: term in text for term in required_terms
        },
    }


def _check(passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"passed": bool(passed), "evidence": evidence}


def _public_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_public_payload(payload)
    return sanitized if isinstance(sanitized, dict) else {}


def _contains_private_marker(payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    return any(marker in text for marker in PRIVATE_MARKERS)


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)

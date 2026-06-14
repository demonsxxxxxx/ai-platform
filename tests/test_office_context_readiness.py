import json
import os
import subprocess
import sys

from app.office_context_readiness import (
    build_office_context_readiness,
    render_office_context_readiness_markdown,
)


def test_office_context_readiness_defines_safe_context_pack_contract_without_enabling_runtime():
    readiness = build_office_context_readiness()

    assert readiness["schema_version"] == "ai-platform.office-context-pack-readiness.v1"
    assert readiness["gate"] == "G6/G9/#22 Office Context Pack Architecture"
    assert readiness["status"] == "partial_blocked"
    assert readiness["issue"] == "#22"
    assert readiness["policy"] == {
        "default_office_execution_tier": "sdk_only_writing",
        "lightweight_office_tasks_start_sandbox_by_default": False,
        "ordinary_user_policy": "public_projection_only",
        "long_term_memory_policy": "fail_closed_until_policy_and_acceptance",
        "does_not_expand_multi_agent_beta": True,
    }
    assert readiness["implemented_controls"] == [
        "source_level_context_pack_contract",
        "context_snapshot_public_provenance_projection_contract",
        "executor_context_pack_prompt_injection_source_tests",
        "source_level_context_pack_persistence_and_versioning",
        "user_visible_context_provenance_api_projection_source_tests",
        "office_execution_tier_router_source_tests",
        "sandbox_cold_start_latency_split_source_contract",
    ]
    assert "persistence/versioning" in readiness["evidence_policy"]
    assert "versioned persistence" not in readiness["evidence_policy"]
    assert "211 executor smoke" in readiness["evidence_policy"]
    assert "frontend acceptance" in readiness["evidence_policy"]
    assert "document-centric follow-up state" in readiness["evidence_policy"]
    assert "211 sandbox latency split smoke" in readiness["evidence_policy"]

    context_pack = readiness["context_pack_contract"]
    assert context_pack["bounded_summary_required"] is True
    assert context_pack["allowed_sources"] == [
        "uploaded_source_documents",
        "previous_generated_artifacts",
        "user_instructions",
        "department_templates",
        "terminology_glossary",
        "meeting_notes",
        "accepted_style_preferences",
    ]
    assert context_pack["user_visible_projection"] == [
        "referenced_materials",
        "used_context_summary",
        "latest_artifact_version",
        "execution_tier",
        "context_pack_version",
        "context_pack_generated_at",
    ]
    assert context_pack["forbidden_projection_terms"] == [
        "executor_private_payload",
        "raw_storage_key",
        "sandbox_workdir",
        "secret_like_values",
        "absolute_runtime_paths",
    ]

    tier_ids = [tier["id"] for tier in readiness["execution_tiers"]]
    assert tier_ids == ["sdk_only_writing", "document_worker", "heavy_sandbox"]
    assert readiness["execution_tiers"][0]["uses_sandbox_by_default"] is False
    assert readiness["execution_tiers"][0]["task_examples"] == [
        "rewrite",
        "summarize",
        "translate",
        "proposal_followup",
    ]
    assert readiness["execution_tiers"][2]["uses_sandbox_by_default"] is True
    assert "script_execution" in readiness["execution_tiers"][2]["task_examples"]

    sandbox_latency = readiness["sandbox_latency_observability"]
    assert sandbox_latency == {
        "status": "source_contract_defined_runtime_acceptance_required",
        "applies_to_execution_tiers": ["heavy_sandbox"],
        "required_metric_fields": [
            "sandbox_lease_acquire_latency_ms",
            "sandbox_container_cold_start_latency_ms",
            "sandbox_healthcheck_latency_ms",
            "executor_model_latency_ms",
            "document_processing_latency_ms",
            "sandbox_cleanup_latency_ms",
        ],
        "must_not_hide_cold_start_in_executor_latency": True,
        "runtime_acceptance_required": "211_sandbox_latency_split_smoke",
    }

    assert readiness["open_gaps"] == [
        "executor_context_pack_211_acceptance",
        "document_centric_followup_state",
        "sandbox_cold_start_latency_split_211_acceptance",
        "frontend_context_provenance_acceptance",
    ]
    assert readiness["non_goals"] == [
        "do_not_start_docker_sandbox_for_lightweight_writing_by_default",
        "do_not_expose_raw_storage_keys_or_executor_private_payloads",
        "do_not_enable_long_term_cross_session_memory_by_default",
        "do_not_expand_g8_g10_multi_agent_to_ordinary_users",
    ]

    serialized = json.dumps(readiness, ensure_ascii=False).lower()
    assert "storage_key" not in serialized.replace("raw_storage_key", "")
    assert serialized.count("sandbox_workdir") == 1
    assert serialized.count('"executor_private_payload"') == 1
    assert "c:\\users" not in serialized
    assert "sk-secret" not in serialized
    assert "callback-token" not in serialized


def test_office_context_readiness_markdown_is_gap_first_and_operator_readable():
    markdown = render_office_context_readiness_markdown(build_office_context_readiness())
    open_gaps_section = markdown.split("## Implemented Controls", 1)[0]

    assert "# ai-platform Office Context Pack Readiness" in markdown
    assert "Status: `partial_blocked`" in markdown
    assert "## Open Gaps" in markdown
    assert "source_level_context_pack_persistence_and_versioning" in markdown
    assert "user_visible_context_provenance_api_projection_source_tests" in markdown
    assert "- office_context_pack_persistence_and_versioning" not in markdown
    assert "executor_context_pack_prompt_injection_source_tests" in markdown
    assert "office_execution_tier_router_source_tests" in markdown
    assert "- office_execution_tier_router\n" not in open_gaps_section
    assert "sdk_only_writing" in markdown
    assert "heavy_sandbox" in markdown
    assert "raw_storage_key" in markdown
    assert "sk-secret" not in markdown
    assert "callback-token" not in markdown


def test_office_context_readiness_cli_outputs_json_without_secret_markers():
    env = os.environ.copy()
    env["SANDBOX_CALLBACK_TOKEN"] = "callback-token"
    env["OPENAI_API_KEY"] = "sk-secret"
    result = subprocess.run(
        [sys.executable, "tools/office_context_readiness.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai-platform.office-context-pack-readiness.v1"
    assert payload["status"] == "partial_blocked"
    assert payload["policy"]["lightweight_office_tasks_start_sandbox_by_default"] is False
    assert "executor_context_pack_prompt_injection_source_tests" in payload["implemented_controls"]
    assert "source_level_context_pack_persistence_and_versioning" in payload["implemented_controls"]
    assert "user_visible_context_provenance_api_projection_source_tests" in payload["implemented_controls"]
    assert "office_execution_tier_router_source_tests" in payload["implemented_controls"]
    assert "office_context_pack_persistence_and_versioning" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split_source_contract" in payload["implemented_controls"]
    assert payload["sandbox_latency_observability"]["must_not_hide_cold_start_in_executor_latency"] is True
    assert "executor_context_pack_injection" not in payload["open_gaps"]
    assert "user_visible_context_provenance_projection" not in payload["open_gaps"]
    assert "executor_context_pack_211_acceptance" in payload["open_gaps"]
    assert "office_execution_tier_router" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split" not in payload["open_gaps"]
    assert "sandbox_cold_start_latency_split_211_acceptance" in payload["open_gaps"]
    assert "sk-secret" not in result.stdout
    assert "callback-token" not in result.stdout

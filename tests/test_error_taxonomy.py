import json

from app.error_taxonomy import (
    ERROR_TAXONOMY_SCHEMA_VERSION,
    build_error_taxonomy_contract,
    classify_error_code,
    summarize_error_categories,
)


def test_error_taxonomy_contract_defines_stable_g9_categories_without_secrets():
    contract = build_error_taxonomy_contract()

    assert contract["schema_version"] == ERROR_TAXONOMY_SCHEMA_VERSION
    assert contract["category_count"] >= 10
    assert set(contract["categories"]) >= {
        "executor",
        "tool",
        "tool_permission",
        "sandbox",
        "model_gateway",
        "queue",
        "database",
        "memory_context",
        "artifact",
        "auth_policy",
        "unknown",
    }
    assert contract["unknown_category"] == "unknown"
    assert contract["projection_policy"] == "category_counts_only_no_raw_error_payload"

    serialized = json.dumps(contract, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "authorization" not in serialized
    assert "sandbox_workspace_root" not in serialized


def test_classify_error_code_maps_platform_failures_to_taxonomy_categories():
    examples = {
        "executor_failure": "executor",
        "worker_process_exception": "executor",
        "mcp_tool_disabled": "tool",
        "tool_permission_denied": "tool_permission",
        "sandbox_runtime_cleanup_failed": "sandbox",
        "model_gateway_timeout": "model_gateway",
        "queue_lease_timeout": "queue",
        "database_pool_waiting": "database",
        "memory_retention_cleanup_failed": "memory_context",
        "artifact_write_failed": "artifact",
        "not_ai_admin": "auth_policy",
        "callback-token=secret /tmp/private-path": "unknown",
    }

    assert {code: classify_error_code(code) for code in examples} == examples


def test_summarize_error_categories_aggregates_counts_and_ignores_invalid_values():
    assert summarize_error_categories(
        {
            "executor_failure": 2,
            "worker_process_exception": 3,
            "sandbox_runtime_cleanup_failed": 1,
            "model_gateway_timeout": 4,
            "artifact_write_failed": 0,
            "database_pool_waiting": True,
            "unmapped-secret-token": 5,
        }
    ) == {
        "executor": 5,
        "sandbox": 1,
        "model_gateway": 4,
        "unknown": 5,
    }

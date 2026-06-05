from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    RUN_CONTRACT_VERSION,
    RUN_PAYLOAD_SCHEMA_VERSION,
    SKILL_MANIFEST_SCHEMA_VERSION,
    STANDARD_EVENT_TYPES,
    TOOL_POLICY_SCHEMA_VERSION,
    ContextSnapshot,
    EventEnvelope,
    SkillManifest,
    ToolPolicy,
    artifact_lineage_contract,
    artifact_manifest_contract,
    is_standard_event_type,
    sanitize_public_payload,
    sanitize_public_text,
    standard_error_code,
    standard_trace_id,
)
from app.projection_redaction import redact_raw_skill_references, sanitize_user_control_input


def test_control_plane_versions_are_stable():
    assert RUN_CONTRACT_VERSION == "ai-platform.run.v1"
    assert RUN_PAYLOAD_SCHEMA_VERSION == "ai-platform.run-payload.v1"
    assert EVENT_ENVELOPE_SCHEMA_VERSION == "ai-platform.event-envelope.v1"
    assert ARTIFACT_MANIFEST_SCHEMA_VERSION == "ai-platform.artifact-manifest.v1"
    assert SKILL_MANIFEST_SCHEMA_VERSION == "ai-platform.skill-manifest.v1"
    assert TOOL_POLICY_SCHEMA_VERSION == "ai-platform.tool-policy.v1"
    assert CONTEXT_SNAPSHOT_SCHEMA_VERSION == "ai-platform.context-snapshot.v1"


def test_trace_ids_and_error_codes_are_normalized():
    assert standard_trace_id("run_abc").startswith("trace_")
    assert standard_trace_id().startswith("trace_")
    assert standard_error_code("executor_failure") == "executor_failure"
    assert standard_error_code("") == "unknown_error"


def test_standard_event_taxonomy_covers_g2_lifecycle_events():
    assert "queued" in STANDARD_EVENT_TYPES
    assert "skill_selected" in STANDARD_EVENT_TYPES
    assert "artifact_created" in STANDARD_EVENT_TYPES
    assert "mcp_tool_call_completed" in STANDARD_EVENT_TYPES
    assert "context_snapshot_created" in STANDARD_EVENT_TYPES
    assert "tool_permission_requested" in STANDARD_EVENT_TYPES
    assert "sandbox_lease_created" in STANDARD_EVENT_TYPES
    assert "checkpoint_created" in STANDARD_EVENT_TYPES
    assert "subagent_started" in STANDARD_EVENT_TYPES
    assert "subagent_completed" in STANDARD_EVENT_TYPES
    assert "subagent_failed" in STANDARD_EVENT_TYPES
    assert "multi_agent_parent_finalized" in STANDARD_EVENT_TYPES
    assert is_standard_event_type("run_succeeded") is True
    assert is_standard_event_type("unknown_custom_event") is False
    assert standard_error_code(None) == "unknown_error"


def test_g2_canonical_placeholder_models_are_contract_only():
    event = EventEnvelope(run_id="run-a", trace_id="trace_a", type="queued", stage="queue")
    assert event.schema_version == "ai-platform.event-envelope.v1"
    assert event.severity == "info"
    assert event.visible_to_user is True
    assert event.token_counts == {}
    assert event.cost == {}

    skill = SkillManifest(skill_id="qa-file-reviewer", version="0.1.0", source="builtin")
    assert skill.schema_version == "ai-platform.skill-manifest.v1"

    policy = ToolPolicy(tool_id="ragflow-knowledge-search", decision="deny")
    assert policy.schema_version == "ai-platform.tool-policy.v1"

    snapshot = ContextSnapshot(run_id="run-a", trace_id="trace_a")
    assert snapshot.schema_version == "ai-platform.context-snapshot.v1"
    assert snapshot.included_memory_record_ids == []


def test_artifact_manifest_contract_platform_fields_cannot_be_overridden():
    manifest = artifact_manifest_contract(
        artifact_type="reviewed_docx",
        manifest={
            "schema_version": "executor.private.v0",
            "artifact_type": "private_type",
            "storage_key": "tenants/default/private.docx",
            "source_file_id": "file-a",
        },
    )

    assert manifest["schema_version"] == "ai-platform.artifact-manifest.v1"
    assert manifest["artifact_type"] == "reviewed_docx"
    assert manifest["source_file_id"] == "file-a"
    assert "storage_key" not in str(manifest)


def test_artifact_lineage_contract_rejects_unsafe_values_in_allowed_keys():
    lineage = artifact_lineage_contract(
        {
            "source_run_id": "run-a",
            "source_event_id": "evt-a",
            "source_step_id": "step-a",
            "source_file_id": "file-a",
            "producer_kind": "subagent",
            "producer_role": "reviewer",
            "checkpoint_id": "checkpoint-a",
            "subagent_id": "subagent-a",
        },
        source_run_id="run-a",
    )
    assert lineage == {
        "source_run_id": "run-a",
        "source_event_id": "evt-a",
        "source_step_id": "step-a",
        "source_file_id": "file-a",
        "producer_kind": "subagent",
        "producer_role": "reviewer",
        "checkpoint_id": "checkpoint-a",
        "subagent_id": "subagent-a",
    }

    unsafe = artifact_lineage_contract(
        {
            "source_run_id": "qa-file-reviewer",
            "source_event_id": "tenants/tenant-a/private/event",
            "source_step_id": "C:/agent-workspaces/run-a/step",
            "source_file_id": "f" * 64,
            "producer_kind": "qa-file-reviewer",
            "producer_role": "qa-file-reviewer",
            "checkpoint_id": "a" * 64,
            "subagent_id": "tenants/tenant-a/private/subagent",
        }
    )

    assert unsafe == {}


def test_public_payload_sanitizer_removes_runtime_private_aliases():
    payload = sanitize_public_payload(
        {
            "message": "done",
            "worker_path": "/app/worker.py",
            "workerPath": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
            "runtime_private_payload": {"token": "hidden"},
            "runtimePrivatePayload": {"token": "hidden"},
            "private_payload": {"cwd": "/tmp/run"},
            "executor_payload": {"adapter_version": "private-adapter"},
            "executorPayload": {"adapterVersion": "private-adapter"},
            "nested": {
                "path": "/app/runtime/private.py",
                "var_path": "/var/lib/ai-platform/private.log",
                "message": "failed in /home/xinlin.jiang/qa-review-queue-runtime",
            },
        }
    )

    assert payload == {"message": "done", "nested": {}}


def test_public_payload_sanitizer_redacts_secret_like_executor_values():
    payload = sanitize_public_payload(
        {
            "message": (
                "callback failed with api_key=sk-live "
                "authorization: Bearer bearer-token-123 user@example.com "
                "clientsecret=compact-client-secret githubtoken=compact-github-token "
                "passwordhash=compact-password-hash secretkey=compact-secret-key "
                "clientcredentialblob=compact-client-credential "
                "secretarysecret=compact-secretary-secret "
                "authorizationbearer=compact-authorization-bearer "
                "privatekey=compact-private-key "
                "bearer=compact-bearer "
                "authkey=compact-auth-key"
            ),
            "openai_api_key": "sk-openai",
            "openaiapikey": "sk-openai-compact",
            "clientSecret": "client-secret",
            "clientsecret": "client-secret-compact",
            "passwordhash": "password-hash-compact",
            "passworddigest": "password-digest-compact",
            "secretvalue": "secret-value-compact",
            "secretkey": "secret-key-compact",
            "secretarysecret": "secretary-secret-compact",
            "clientsecretarysecret": "client-secretary-secret-compact",
            "authorizationbearer": "authorization-bearer-compact",
            "authorizationvalue": "authorization-value-compact",
            "accesskeyid": "access-key-id-compact",
            "awsaccesskeyid": "aws-access-key-id-compact",
            "privatekey": "private-key-compact",
            "sshprivatekey": "ssh-private-key-compact",
            "bearer": "bearer-compact",
            "bearervalue": "bearer-value-compact",
            "bearerkey": "bearer-key-compact",
            "authkey": "auth-key-compact",
            "authheader": "auth-header-compact",
            "authvalue": "auth-value-compact",
            "authstatus": "approved",
            "publickey": "public-key-visible",
            "github_token": "ghp_secret_raw",
            "githubtoken": "ghp-secret-compact",
            "slack_access_token": "slack-secret-raw",
            "slackaccesstoken": "slack-access-compact",
            "idtoken": "id-token-compact",
            "authtoken": "auth-token-compact",
            "xapikey": "sk-x-api-key-compact",
            "credential_blob": "credential-secret-raw",
            "credentialblob": "credential-secret-compact",
            "clientcredentialblob": "client-credential-secret-compact",
            "servicecredentialsjson": "service-credentials-secret-compact",
            "authorizationHeader": "Bearer authorization-header-secret",
            "authorizationheader": "Bearer authorization-header-compact",
            "x_api_key": "sk-x-api-key",
            "token_count_github_token": "ghp-count-secret",
            "token_usage_slack_token": "slack-usage-secret",
            "token_count_slack_access_token": "slack-access-secret",
            "nested": {
                "note": "{\"client_secret\":\"client-json\"} token=nested-token",
                "headers": {"Authorization": "Bearer nested-bearer-token"},
                "punctuation": "prefix smoke-secret-token. suffix smoke-secret-token-",
                "safe": "done",
            },
        }
    )

    serialized = str(payload)
    assert payload == {
        "message": (
            "callback failed with api_key=[redacted-secret] "
            "authorization=[redacted-secret] [redacted-email] "
            "clientsecret=[redacted-secret] githubtoken=[redacted-secret] "
            "passwordhash=[redacted-secret] secretkey=[redacted-secret] "
            "clientcredentialblob=[redacted-secret] "
            "secretarysecret=[redacted-secret] "
            "authorizationbearer=[redacted-secret] "
            "privatekey=[redacted-secret] "
            "bearer=[redacted-secret] "
            "authkey=[redacted-secret]"
        ),
        "nested": {
            "note": "{\"client_secret\":\"[redacted-secret]\"} token=[redacted-secret]",
            "headers": {},
            "punctuation": "prefix [redacted-secret]. suffix [redacted-secret]-",
            "safe": "done",
        },
        "authstatus": "approved",
        "publickey": "public-key-visible",
    }
    assert "sk-live" not in serialized
    assert "compact-client-secret" not in serialized
    assert "compact-github-token" not in serialized
    assert "compact-password-hash" not in serialized
    assert "compact-secret-key" not in serialized
    assert "compact-client-credential" not in serialized
    assert "compact-secretary-secret" not in serialized
    assert "compact-authorization-bearer" not in serialized
    assert "compact-private-key" not in serialized
    assert "compact-bearer" not in serialized
    assert "compact-auth-key" not in serialized
    assert "bearer-token-123" not in serialized
    assert "user@example.com" not in serialized
    assert "sk-openai" not in serialized
    assert "sk-openai-compact" not in serialized
    assert "client-secret" not in serialized
    assert "client-secret-compact" not in serialized
    assert "password-hash-compact" not in serialized
    assert "password-digest-compact" not in serialized
    assert "secret-value-compact" not in serialized
    assert "secret-key-compact" not in serialized
    assert "secretary-secret-compact" not in serialized
    assert "client-secretary-secret-compact" not in serialized
    assert "authorization-bearer-compact" not in serialized
    assert "authorization-value-compact" not in serialized
    assert "access-key-id-compact" not in serialized
    assert "aws-access-key-id-compact" not in serialized
    assert "private-key-compact" not in serialized
    assert "ssh-private-key-compact" not in serialized
    assert "bearer-compact" not in serialized
    assert "bearer-value-compact" not in serialized
    assert "bearer-key-compact" not in serialized
    assert "auth-key-compact" not in serialized
    assert "auth-header-compact" not in serialized
    assert "auth-value-compact" not in serialized
    assert "approved" in serialized
    assert "public-key-visible" in serialized
    assert "ghp_secret_raw" not in serialized
    assert "ghp-secret-compact" not in serialized
    assert "slack-secret-raw" not in serialized
    assert "slack-access-compact" not in serialized
    assert "id-token-compact" not in serialized
    assert "auth-token-compact" not in serialized
    assert "sk-x-api-key-compact" not in serialized
    assert "credential-secret-raw" not in serialized
    assert "credential-secret-compact" not in serialized
    assert "client-credential-secret-compact" not in serialized
    assert "service-credentials-secret-compact" not in serialized
    assert "authorization-header-secret" not in serialized
    assert "authorization-header-compact" not in serialized
    assert "sk-x-api-key" not in serialized
    assert "ghp-count-secret" not in serialized
    assert "slack-usage-secret" not in serialized
    assert "slack-access-secret" not in serialized
    assert "nested-bearer-token" not in serialized
    assert "smoke-secret-token" not in serialized


def test_public_payload_sanitizer_preserves_safe_token_like_text():
    payload = sanitize_public_payload(
        {
            "message": (
                "token_counts: 123 token_budget: 100 "
                "token-budget: 100 auth-token-status: approved "
                "password-reset-flow ready credential-helper available "
                "authorization_status: approved client_secretary: Jane "
                "Bearer workspace team"
            ),
            "clientsecretary": "Jane",
            "client_secretary": "Jane Doe",
            "secretary_name": "Jane",
            "client_secretary_name": "Jane Doe",
            "input_token_count": 12,
            "output_token_count": 8,
            "total_token_count": 20,
            "remaining_token_budget": 100,
            "oauth_authorization_status": "approved",
            "tokenizer": "cl100k_base",
            "nested": {
                "summary": "refresh token_counts: 456 token-budget stable",
                "status": "authorization_status=approved",
            },
        }
    )

    assert payload == {
            "message": (
                "token_counts: 123 token_budget: 100 "
                "token-budget: 100 auth-token-status: approved "
                "password-reset-flow ready credential-helper available "
                "authorization_status: approved client_secretary: Jane "
                "Bearer workspace team"
            ),
            "clientsecretary": "Jane",
            "client_secretary": "Jane Doe",
            "secretary_name": "Jane",
            "client_secretary_name": "Jane Doe",
            "input_token_count": 12,
            "output_token_count": 8,
            "total_token_count": 20,
            "remaining_token_budget": 100,
            "oauth_authorization_status": "approved",
            "tokenizer": "cl100k_base",
            "nested": {
                "summary": "refresh token_counts: 456 token-budget stable",
                "status": "authorization_status=approved",
        },
    }


def test_public_payload_sanitizer_preserves_public_urls_but_drops_runtime_paths():
    payload = sanitize_public_payload(
        {
            "message": "See https://example.com/doc and http://example.com/home",
            "url": "https://example.com/doc",
            "homepage": "http://example.com/home",
            "windowsPath": "C:/agent-workspaces/run-a/output.txt",
            "linuxPath": "/home/xinlin.jiang/qa-review-queue-runtime/output.txt",
            "cwdText": "cwd=C:/Users/Xinlin/file.txt",
            "pathText": "path:C:\\Users\\Xinlin\\file.txt",
            "jsonText": "{\"cwd\":\"C:/Users/Xinlin/file.txt\"}",
            "nested": {
                "docs_url": "https://example.com/a/b?x=1",
                "runtime": "artifact at D:\\agent-workspaces\\run-a\\out.docx",
            },
        }
    )

    assert payload == {
        "message": "See https://example.com/doc and http://example.com/home",
        "url": "https://example.com/doc",
        "homepage": "http://example.com/home",
        "nested": {
            "docs_url": "https://example.com/a/b?x=1",
        },
    }
    assert sanitize_public_text("https://example.com/doc") == "https://example.com/doc"
    assert sanitize_public_text("C:/agent-workspaces/run-a/output.txt") == ""


def test_raw_skill_redaction_removes_camel_case_skill_selectors():
    payload = sanitize_user_control_input(
        {
            "message": "run",
            "skillId": "qa-file-reviewer",
            "skillIds": ["qa-file-reviewer"],
            "allowedSkills": ["qa-file-reviewer"],
            "stagedSkills": ["qa-file-reviewer"],
            "usedSkills": ["qa-file-reviewer"],
            "multiAgentSteps": [
                {
                    "stepKey": "review",
                    "skillIds": ["qa-file-reviewer"],
                    "workerPath": "/home/xinlin.jiang/qa-review-queue-runtime/worker.py",
                }
            ],
        }
    )

    assert payload == {"message": "run", "capability_id": "document_review", "multiAgentSteps": [{"stepKey": "review"}]}
    assert "skillId" not in str(payload)
    assert "skillIds" not in str(payload)
    assert "allowedSkills" not in str(payload)
    assert "qa-file-reviewer" not in str(payload)


def test_raw_skill_redaction_removes_common_skill_selector_aliases():
    payload = sanitize_user_control_input(
        {
            "message": "run",
            "defaultSkillId": "qa-file-reviewer",
            "selectedSkillId": "qa-file-reviewer",
            "requested_skill_id": "qa-file-reviewer",
            "preferredSkillIds": ["qa-file-reviewer"],
            "steps": [
                {
                    "stepKey": "review",
                    "selectedSkillIds": ["qa-file-reviewer"],
                }
            ],
        }
    )

    assert payload == {"message": "run", "capability_id": "document_review", "steps": [{"stepKey": "review"}]}
    assert "SkillId" not in str(payload)
    assert "skill_id" not in str(payload)
    assert "SkillIds" not in str(payload)
    assert "qa-file-reviewer" not in str(payload)


def test_raw_skill_redaction_can_preserve_empty_camel_case_skill_ids_for_compat():
    payload = redact_raw_skill_references({"stepKey": "review", "skillIds": ["qa-file-reviewer"]}, preserve_empty_skill_ids=True)

    assert payload == {"stepKey": "review", "skillIds": []}

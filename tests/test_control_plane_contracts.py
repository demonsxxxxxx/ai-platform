import itertools
import random
import re
import sys

import pytest

from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    CONTEXT_SNAPSHOT_SCHEMA_VERSION,
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
    attach_run_thinking_effort,
    is_standard_event_type,
    sanitize_public_payload,
    sanitize_public_text,
    standard_trace_id,
)
from app.projection_redaction import redact_raw_skill_references, sanitize_user_control_input
from app.platform import public_payload
from app.streaming.domain.run_events import standard_error_code
from app.streaming.events import EVENT_ENVELOPE_SCHEMA_VERSION


def test_control_plane_public_sanitizers_are_platform_identity_aliases():
    assert sanitize_public_payload is public_payload.sanitize_public_payload
    assert sanitize_public_text is public_payload.sanitize_public_text


def test_control_plane_versions_are_stable():
    assert RUN_CONTRACT_VERSION == "ai-platform.run.v1"
    assert RUN_PAYLOAD_SCHEMA_VERSION == "ai-platform.run-payload.v1"
    assert EVENT_ENVELOPE_SCHEMA_VERSION == "ai-platform.event-envelope.v1"
    assert ARTIFACT_MANIFEST_SCHEMA_VERSION == "ai-platform.artifact-manifest.v1"
    assert SKILL_MANIFEST_SCHEMA_VERSION == "ai-platform.skill-manifest.v1"
    assert TOOL_POLICY_SCHEMA_VERSION == "ai-platform.tool-policy.v1"
    assert CONTEXT_SNAPSHOT_SCHEMA_VERSION == "ai-platform.context-snapshot.v1"


def test_run_thinking_effort_omits_auto_and_attaches_explicit_level():
    assert attach_run_thinking_effort({"message": "hello"}, None) == {"message": "hello"}
    assert attach_run_thinking_effort({}, {"enable_thinking": "off"}) == {}
    assert attach_run_thinking_effort({}, {"enable_thinking": "high"}) == {
        "_thinking_effort": "high"
    }


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
    assert "sandbox_lease_created" in STANDARD_EVENT_TYPES
    assert "checkpoint_created" in STANDARD_EVENT_TYPES
    assert "subagent_started" in STANDARD_EVENT_TYPES
    assert "subagent_completed" in STANDARD_EVENT_TYPES
    assert "subagent_failed" in STANDARD_EVENT_TYPES
    assert is_standard_event_type("tool_permission_requested") is False
    assert is_standard_event_type("multi_agent_dispatch_handoff") is False
    assert is_standard_event_type("run_multi_agent_child_created") is False
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
                "runtime_diagnostics": {"token": "hidden"},
            },
            "tuple_payload": (
                {"runtime_private_payload": {"token": "hidden"}, "safe": "done"},
                "/app/runtime/private.py",
            ),
        }
    )

    assert payload == {
        "message": "done",
        "nested": {},
        "tuple_payload": ({"safe": "done"},),
    }


@pytest.mark.parametrize("delegate_used", [False, True])
@pytest.mark.parametrize(
    "delegate_key,boundary_key",
    [
        ("delegate_used", "worker_boundary"),
        ("delegateUsed", "workerBoundary"),
        ("delegate-used", "worker-boundary"),
    ],
)
def test_public_payload_sanitizer_keeps_retired_executor_metadata_private(
    delegate_used, delegate_key, boundary_key,
):
    legacy_metadata = {delegate_key: delegate_used, boundary_key: "historical-executor"}
    historical_result = {
        "message": "Historical answer",
        **legacy_metadata,
        "nested": [dict(legacy_metadata)],
        "tuple_payload": ({"safe": "kept", **legacy_metadata},),
    }

    assert sanitize_public_payload(historical_result) == {
        "message": "Historical answer",
        "nested": [{}],
        "tuple_payload": ({"safe": "kept"},),
    }
    # Reading a historical result must not rewrite the stored private payload.
    assert historical_result[delegate_key] is delegate_used
    assert historical_result[boundary_key] == "historical-executor"
    assert historical_result["nested"] == [legacy_metadata]


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


@pytest.mark.parametrize(
    "secret_text",
    [
        "ordinary api_key=sk-abcdefghi12 text",
        "ordinary Bearer abcdefgh1 text",
        "ordinary abcdefghij.klmnopqrst.uvwxyzabcd text",
    ],
)
def test_public_payload_strict_mode_redacts_provider_secret_corpus_only(
    secret_text: str,
):
    payload = sanitize_public_payload({"message": secret_text})

    assert isinstance(payload, dict)
    assert secret_text not in payload["message"]
    assert "ordinary" in payload["message"]


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


def test_public_payload_sanitizer_keeps_runtime_paths_out_of_payloads():
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


@pytest.mark.parametrize("mode", ["standard", "strict"])
@pytest.mark.parametrize(
    "body",
    [
        'Before password="alpha bravo charlie',
        "Before api_key -> 'alpha bravo,charlie",
        '{"outer":{"client_secret":"alpha\nbravo;charlie',
        'Before auth_header => "alpha \\"bravo\\" charlie\\',
    ],
)
def test_unfinished_quoted_secret_is_redacted_through_eof_by_shared_wrappers(mode, body):
    from app.kernel.memory_redaction import redact_memory_metadata, redact_memory_text

    expected = redact_memory_text(body, mode=mode)
    assert all(word not in expected for word in ("alpha", "bravo", "charlie"))
    assert "[redacted-secret]" in expected
    assert redact_memory_text(expected, mode=mode) == expected
    assert redact_memory_metadata({"note": [body]}, mode=mode) == {"note": [expected]}
    assert sanitize_public_text(body) == expected
    assert sanitize_public_payload({"outer": [{"note": body}]}) == {"outer": [{"note": expected}]}


@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("escaped", [False, True])
def test_secret_quote_continuation_offset_matches_chunk_semantics_without_copy(quote, escaped):
    from app.kernel.memory_redaction import (
        SecretQuoteContinuation,
        sanitizer_unstable_assignment_suffix_length,
    )

    class NoSuffixCopy(str):
        def __getitem__(self, key):
            assert not isinstance(key, slice), "quoted assignment scan copied a suffix"
            return super().__getitem__(key)

    state = SecretQuoteContinuation(quote, escaped)
    prefix = "ignored prefix "
    for suffix in ("", "alpha\\", quote + " after", "alpha\\" + quote + " bravo" + quote + " after"):
        assert state.consume(NoSuffixCopy(prefix + suffix), start=len(prefix)) == state.consume(suffix)
    assert sanitizer_unstable_assignment_suffix_length(
        NoSuffixCopy((f"password={quote}synthetic{quote} " * 32)), max_chars=32,
    ) == 0


@pytest.fixture
def kernel_lexical_oracle():
    # Frozen pre-K2 policy, used only on short synthetic inputs. Keeping spans
    # and callback decisions catches changes that output-only checks conceal.
    token = re.compile(
        r"(?i)(?<!\[)\b(?=[A-Za-z0-9._-]{8,}\b)"
        r"(?:[A-Za-z0-9]+[._-])+(?:secret|token|credential|password)(?:[._-][A-Za-z0-9]+)*\b"
        r"(?![\"']?\s*[:=])(?!\])"
        r"|(?<!\[)\b(?=[A-Za-z0-9._-]{8,}\b)"
        r"(?:secret|token|credential|password)(?:[._-][A-Za-z0-9]+)+\b"
        r"(?![\"']?\s*[:=])(?!\])"
    )
    email = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    followers = {
        "budget", "budgets", "count", "counts", "flow", "flows", "helper",
        "helpers", "reset", "resets", "status", "total", "usage",
    }

    def safe(value):
        segments = [part.lower() for part in re.split(r"[._-]+", value) if part]
        sensitive = [
            index for index, part in enumerate(segments)
            if part in {"secret", "token", "credential", "password"}
        ]
        return bool(sensitive) and all(
            index + 1 < len(segments) and segments[index + 1] in followers
            for index in sensitive
        )

    def replacement(match):
        text = match.string
        end = match.end()
        if end + 1 < len(text) and text[end] in "._-" and text[end + 1].isalnum():
            full_end = end
            while full_end < len(text) and (text[full_end].isalnum() or text[full_end] in "._-"):
                full_end += 1
            if safe(text[match.start():full_end]):
                return match.group()
        return match.group() if safe(match.group()) else "[redacted-secret]"

    return token, replacement, email


@pytest.mark.parametrize("mode", ["standard", "strict"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("x.token..a", "[redacted-secret]..a"),
        ("a.token..é", "[redacted-secret]..é"),
        ("x.token.......", "x.token......."),
        ("[foo.bar.secret.baz]", "[foo.[redacted-secret].baz]"),
        ("éfoo.bar.secret.baz", "éfoo.[redacted-secret]"),
        ("foo_secret_bar", "[redacted-secret]"),
        ("éfoo_secret_bar", "éfoo_secret_bar"),
        ("foo_secret_bar_", "foo_secret_bar_"),
        ("x.secret.foo=1", "[redacted-secret].foo=1"),
        ("a.secret.helper", "a.secret.helper"),
        ("a.ſecret.helper", "[redacted-secret]"),
        ("a.toKen.value", "[redacted-secret]"),
        ("a.credentİal.value", "[redacted-secret]"),
        ("a.credentıal.value", "[redacted-secret]"),
        ("foo.secret.helper.secret.count]", "foo.secret.helper.secret.count]"),
        ("token_count=123", "token_count=123"),
        ("password-reset-flow", "password-reset-flow"),
        ("credential-helper", "credential-helper"),
        ("x@y@z.com", "x@[redacted-email]"),
        ("éabc.x@y.com", "éabc[redacted-email]"),
        ("a@b.com_", "a@b.com_"),
        ("a@b.comé", "a@b.comé"),
        ("a@b.com9.def", "[redacted-email]"),
        ("a@b.com.x-y@z.com", "[redacted-email][redacted-email]"),
        ("a@..com", "[redacted-email]"),
        ("a@.com", "a@.com"),
        ("[redacted-secret] [redacted-email]", "[redacted-secret] [redacted-email]"),
    ],
)
def test_kernel_lexical_redaction_preserves_policy(text, expected, mode):
    from app.kernel.memory_redaction import redact_memory_text

    assert redact_memory_text(text, mode=mode) == expected
    assert redact_memory_text(expected, mode=mode) == expected


def test_kernel_lexical_redaction_matches_bounded_oracle(kernel_lexical_oracle):
    from app.kernel import memory_redaction as kernel

    token, replacement, email = kernel_lexical_oracle
    segments = ["a", "token", "secret", "helper", "count", "ſecret", "toKen", "İ", "ı", "é", "猫"]
    cases = [
        prefix + left + separator + right + suffix
        for left, separator, right in itertools.product(segments, [".", "_", "..", ".-"], segments)
        for prefix, suffix in [("", ""), ("[", "]"), ("é", "_"), ("", "..é"), ("", "' :=1")]
    ]
    rng = random.Random(82951)
    pieces = segments + ["password", "credential", "SECRET", "-", "__", " ", "@", "[", "]", ":", "=", '"', "'", "\\", "\n", "\u0301", "²", "ß", "%", "+", "/", "com", "com9"]
    cases.extend("".join(rng.choices(pieces, k=rng.randrange(1, 19))) for _ in range(3000))
    for text in cases:
        assert len(text) <= 192  # The retired regex is never used for large-work tests.
        assert list(kernel._secret_like_token_matches(text)) == [
            (*match.span(), replacement(match) == "[redacted-secret]")
            for match in token.finditer(text)
        ], text
        assert list(kernel._email_matches(text)) == [match.span() for match in email.finditer(text)], text
        assert kernel._redact_secret_like_values(text) == token.sub(replacement, text), text
        assert kernel._redact_emails(text) == email.sub("[redacted-email]", text), text


def test_kernel_lexical_redaction_preserves_modes_and_wrappers(monkeypatch, kernel_lexical_oracle):
    from app.kernel import memory_redaction as kernel

    token, replacement, email = kernel_lexical_oracle
    texts = [
        "safe token_count=123 password-reset-flow credential-helper",
        "[foo.bar.secret.baz] a.ſecret.helper a.secret.é.a.secret.é.",
        "a@b.com.x-y@z.com and éabc.x@y.com",
        "sk-demoABCDEFGHIJKLMN abcdefghij.klmnopqrst.uvwxyzABCD",
        "[redacted-secret] [redacted-email]",
        'api_key="synthetic \\"quote\\" value" next=x.token..a',
        "client_secret: 'synthetic unfinished value",
    ]
    payload = {"note": texts, "token_count": 123, "api_key": "synthetic-value"}
    actual = {
        mode: ([kernel.redact_memory_text(text, mode=mode) for text in texts],
               kernel.redact_memory_metadata(payload, mode=mode))
        for mode in ("standard", "strict")
    }
    actual_public = sanitize_public_payload(payload)
    actual_text = [sanitize_public_text(text) for text in texts]
    monkeypatch.setattr(kernel, "_redact_secret_like_values", lambda text: token.sub(replacement, text))
    monkeypatch.setattr(kernel, "_redact_emails", lambda text: email.sub("[redacted-email]", text))
    for mode in ("standard", "strict"):
        assert actual[mode] == (
            [kernel.redact_memory_text(text, mode=mode) for text in texts],
            kernel.redact_memory_metadata(payload, mode=mode),
        )
    assert actual_public == sanitize_public_payload(payload)
    assert actual_text == [sanitize_public_text(text) for text in texts]
    assert actual["standard"][0][3] == texts[3]
    assert actual["strict"][0][3] == "[redacted-secret] [redacted-secret]"


@pytest.mark.parametrize(
    "unit",
    ["a.", "_a.secret_", "a.secret.é.", "secret.helper.é.", "a@b.com.x-y@z.com ", "a." * 8 + "@..comé "],
)
def test_kernel_lexical_redaction_has_linear_scan_work(monkeypatch, unit):
    from app.kernel import memory_redaction as kernel

    # Count Python scan steps, inspected/copied string units, and the input
    # lengths supplied to each simple regex primitive. This exercises matching,
    # safe-suffix classification and output assembly without wall-clock limits.
    work = [0]

    class MeteredText(str):
        def __contains__(self, value):
            work[0] += len(self)
            return super().__contains__(value)

        def __getitem__(self, index):
            value = super().__getitem__(index)
            work[0] += len(value)
            return MeteredText(value)

        def lower(self):
            work[0] += len(self)
            return MeteredText(super().lower())

    class MeteredPattern:
        def __init__(self, pattern, name):
            self.pattern = pattern
            self.name = name

        def finditer(self, text, start=0, end=None):
            end = len(text) if end is None else end
            work[0] += end - start
            return self.pattern.finditer(text, start, end)

        def fullmatch(self, text):
            work[0] += len(text)
            return self.pattern.fullmatch(text)

        def search(self, text):
            work[0] += len(text)
            return self.pattern.search(text)

        def match(self, text, start):
            if self.name == "_EMAIL_DOMAIN_PATTERN":
                region = re.compile(r"[A-Za-z0-9.-]*").match(text, start)
            else:
                region = re.compile(r"[\"']?\s*").match(text, start)
            work[0] += region.end() - start + 1
            return self.pattern.match(text, start)

    names = [
        "_SECRET_LIKE_RUN_PATTERN", "_SECRET_LIKE_SEGMENT_PATTERN", "_SECRET_LIKE_ALNUM_PATTERN",
        "_SECRET_LIKE_MARKER_PATTERN", "_SECRET_LIKE_ASSIGNMENT_FOLLOWER_PATTERN",
        "_EMAIL_LOCAL_RUN_PATTERN", "_EMAIL_DOMAIN_PATTERN",
    ]
    for name in names:
        monkeypatch.setattr(kernel, name, MeteredPattern(getattr(kernel, name), name))

    def trace(frame, event, arg):
        if event == "line" and frame.f_code.co_filename == kernel.__file__:
            work[0] += 1
        return trace

    totals = []
    for repetitions in (128, 256, 512):
        text = MeteredText(unit * repetitions)
        work[0] = 0
        previous_trace = sys.gettrace()
        try:
            sys.settrace(trace)
            kernel._redact_secret_like_values(text)
            kernel._redact_emails(text)
        finally:
            sys.settrace(previous_trace)
        totals.append(work[0])
        assert work[0] < 200 * len(text)
    assert totals[1] <= 2.1 * totals[0]
    assert totals[2] <= 2.1 * totals[1]


@pytest.mark.parametrize(
    "text",
    [
        "The report is ready. Please review the introduction before the meeting.",
        "x " * 409600 + "x",
        "a." * 2048,
    ],
    ids=["plain-small", "plain-large", "dotted-marker-free"],
)
def test_kernel_lexical_absence_checks_skip_per_word_scans(monkeypatch, text):
    from app.kernel import memory_redaction as kernel

    class UnexpectedRunScan:
        def finditer(self, *args):
            pytest.fail("marker-free, no-@ prose entered the per-word run scanner")

    # A work invariant for the large plain-narration path: its cost must not
    # include one Python iteration/allocation per word. No wall-clock threshold.
    monkeypatch.setattr(kernel, "_SECRET_LIKE_RUN_PATTERN", UnexpectedRunScan())
    monkeypatch.setattr(kernel, "_EMAIL_LOCAL_RUN_PATTERN", UnexpectedRunScan())
    assert kernel._redact_secret_like_values(text) == text
    assert kernel._redact_emails(text) == text

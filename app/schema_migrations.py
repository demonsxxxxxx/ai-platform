"""Versioned, serialized PostgreSQL schema application and readiness checks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from app.db import SCHEMA_PATH, close_pool, connect, transaction


V4_PUBLICATION_SCHEMA_VERSION = "2026.08.24.1"
V4_SUCCESSOR_REBUILD_SCHEMA_VERSION = "2026.08.25.1"
V4_PENDING_ADMISSION_SCHEMA_VERSION = "2026.08.26.2"
V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION = "2026.08.27.1"
V4_CONCURRENT_DUE_INDEX_SCHEMA_VERSION = "2026.08.27.2"
MODEL_CONTROL_PLANE_SCHEMA_VERSION = "2026.08.28.1"
MCP_DYNAMIC_TOOL_DISCOVERY_SCHEMA_VERSION = "2026.08.29.1"
RUN_ATTEMPT_RECONCILER_TAKEOVER_SCHEMA_VERSION = "2026.08.30.1"
RUN_ATTEMPT_HEARTBEAT_MONOTONICITY_SCHEMA_VERSION = "2026.08.30.2"
RUN_ATTEMPT_HEARTBEAT_CLOCK_SAFETY_SCHEMA_VERSION = "2026.08.30.3"
EXPERT_MARKET_SCHEMA_VERSION = "2026.09.01.1"
AGENT_AVATAR_STYLE_SCHEMA_VERSION = "2026.09.01.2"
USER_PROFILE_METADATA_SCHEMA_VERSION = "2026.09.02.1"
FILE_UPLOAD_SESSION_SCHEMA_VERSION = "2026.09.03.1"
CLAUDE_PROVIDER_SESSION_SCHEMA_VERSION = "2026.09.04.1"
EXPERT_SKILL_NAME_SCHEMA_VERSION = "2026.09.03.2"
BAOYU_TRANSLATE_RETIREMENT_SCHEMA_VERSION = "2026.09.07.1"
EXPERT_MARKET_MULTI_TAG_SCHEMA_VERSION = "2026.09.07.2"
STREAM_ONLY_SCHEMA_VERSION = "2026.09.12.1"
RUN_DIAGNOSTICS_SCHEMA_VERSION = "2026.09.13.1"
MODEL_TOKEN_LIMIT_EXPAND_SCHEMA_VERSION = "2026.09.15.1"
CLAUDE_CONTEXT_CUTOVER_SCHEMA_VERSION = "2026.09.15.2"
SANDBOX_PROVIDER_RENEWAL_SCHEMA_VERSION = "2026.09.16.1"
REPOSITORY_SKILL_RETIREMENT_SCHEMA_VERSION = "2026.09.22.1"
HUMAN_APPROVAL_AND_LEGACY_MULTI_AGENT_RETIREMENT_SCHEMA_VERSION = "2026.09.26.1"
RUN_INPUTS_SCHEMA_VERSION = "2026.10.07.1"
ASSISTANT_TEXT_PART_INDEX_SCHEMA_VERSION = "2026.10.09.1"
PROVIDER_TRANSCRIPT_REPRESENTATION_SCHEMA_VERSION = "2026.10.10.1"
RUN_QUESTION_DEFAULT_ANSWER_SCHEMA_VERSION = "2026.10.12.1"
TERMINAL_RECONCILIATION_RETRY_SCHEMA_VERSION = "2026.10.13.1"
PROVIDER_SESSION_IMAGES_SCHEMA_VERSION = "2026.10.14.1"
MCP_ENTERPRISE_GATEWAY_SCHEMA_VERSION = "2026.10.15.1"
MODEL_USER_ACCESS_SCHEMA_VERSION = "2026.10.20.1"
MODEL_TOKEN_USAGE_SCHEMA_VERSION = "2026.10.20.2"
MODEL_AUTO_COMPACT_WINDOW_SCHEMA_VERSION = "2026.10.22.1"
RUN_STATUS_SCHEMA_VERSION = "2026.10.25.1"
RUN_DELIVERY_INTENTS_SCHEMA_VERSION = "2026.10.25.2"
SANDBOX_EXECUTION_DISPATCHES_SCHEMA_VERSION = "2026.10.25.3"
RUN_ATTEMPT_SETTLEMENT_PROJECTION_SCHEMA_VERSION = "2026.10.25.4"
RUN_ATTEMPT_SETTLEMENT_SHADOW_CONTEXT_SCHEMA_VERSION = "2026.10.25.5"
EXECUTION_SUPERVISION_SCHEMA_VERSION = "2026.10.25.6"
EXECUTION_SUPERVISION_RECOVERY_SCHEMA_VERSION = "2026.10.25.7"
RUN_ATTEMPT_SETTLEMENT_STORAGE_SCHEMA_VERSION = "2026.10.25.8"
# N42 may be enabled only after the N40 reader cutover has a durable migration marker.
N42_REQUIRED_N40_SCHEMA_VERSION = "2026.10.25.8"
RUN_STALE_RECONCILIATION_CLAIMS_SCHEMA_VERSION = "2026.10.25.9"
RUN_ATTEMPT_SETTLEMENT_WRITER_SCHEMA_VERSION = "2026.10.25.10"
TARGET_SCHEMA_VERSION = RUN_ATTEMPT_SETTLEMENT_WRITER_SCHEMA_VERSION
# Concurrent-index authority advances only when its exact index contract changes.
# The Stream-only cutover retires old index contracts and is not binary rollback-compatible.
CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION = RUN_ATTEMPT_SETTLEMENT_WRITER_SCHEMA_VERSION
RUN_ATTEMPT_FUTURE_HEARTBEAT_TOLERANCE_SECONDS = 5
RUN_STATUS_CONSTRAINT_NAME = "chk_runs_status"
RUN_STATUS_LOCK_TIMEOUT = "2s"
RUN_STATUS_VALIDATION_TIMEOUT = "30s"
RUN_STATUS_CHECK_EXPRESSION = """status is not null
  and status = any (array[
    'queued'::text, 'running'::text, 'cancel_requested'::text,
    'succeeded'::text, 'failed'::text, 'cancelled'::text,
    'canceled'::text, 'dead-letter'::text, 'dead_letter'::text,
    'dead-lettered'::text
  ])"""
RUN_STATUS_CHECK_DEFINITION = "CHECK (status IS NOT NULL AND (status = ANY (ARRAY[" \
    "'queued'::text, 'running'::text, 'cancel_requested'::text, " \
    "'succeeded'::text, 'failed'::text, 'cancelled'::text, " \
    "'canceled'::text, 'dead-letter'::text, 'dead_letter'::text, " \
    "'dead-lettered'::text])))"
MIGRATION_LOCK_ID = 7_226_391_831_505_901_103
INDEX_MIGRATION_LOCK_ID = 7_226_391_831_505_901_104
CRITICAL_RELATIONS = (
    "schema_migrations",
    "schema_index_migrations",
    "users",
    "runs",
    "run_diagnostics",
    "model_token_usage_scopes",
    "model_token_usage_observations",
    "model_token_usage_coverage",
    "model_token_usage_deltas",
    "model_gateway_revisions",
    "model_catalog_entries",
    "run_attempts",
    "run_attempt_settlements",
    "run_attempt_settlement_storage",
    "run_attempt_settlement_writer",
    "run_stale_reconciliation_claims",
    "execution_supervision_domains",
    "execution_supervision_claims",
    "run_input_sessions",
    "run_input_questions",
    "run_inputs",
    "run_skill_materializations",
    "run_events",
    "agent_profile_favorites",
    "sse_stream_authorities",
    "messages",
    "files",
    "file_upload_sessions",
    "artifacts",
    "object_deletion_outbox",
    "run_delivery_intents",
    "audit_logs",
    "sandbox_leases",
    "sandbox_execution_dispatches",
    "mcp_servers",
    "mcp_server_credentials",
    "mcp_tools",
    "provider_session_heads",
    "provider_session_epochs",
    "provider_session_entries",
    "provider_session_images",
    "provider_session_append_receipts",
    "provider_turn_receipts",
    "conversation_context_checkpoints",
    "run_context_snapshots",
)
RETIRED_RELATIONS = ("run_tool_permission_requests",)
CRITICAL_COLUMNS = (
    ("schema_migrations", "state", "text", True),
    ("mcp_servers", "integration_kind", "text", True),
    ("users", "metadata_json", "jsonb", True),
    ("sessions", "title_source", "text", True),
    ("agent_profile_revisions", "skill_set", "jsonb", True),
    ("agent_profile_revisions", "avatar_seed", "text", True),
    ("agent_profile_revisions", "market_tags", "jsonb", True),
    ("runs", "execution_kind", "text", True),
    ("runs", "skill_id", "text", False),
    ("runs", "authz_policy_version", "int4", True),
    ("runs", "authority_source", "text", True),
    ("runs", "authority_checked_at", "timestamptz", False),
    ("runs", "model_id", "text", False),
    ("runs", "model_value", "text", False),
    ("runs", "model_gateway_revision", "int8", False),
    ("runs", "auto_compact_window", "int8", False),
    ("runs", "max_output_tokens", "int8", False),
    ("runs", "terminalization_target", "text", False),
    ("runs", "terminalization_reason", "text", True),
    ("runs", "terminalization_result_json", "jsonb", True),
    ("runs", "terminalization_error_code", "text", False),
    ("runs", "terminalization_error_message", "text", False),
    ("run_diagnostics", "diagnostic_id", "text", True),
    ("run_diagnostics", "tenant_id", "text", True),
    ("run_diagnostics", "run_id", "text", True),
    ("run_diagnostics", "schema_version", "text", True),
    ("run_diagnostics", "revision", "int8", True),
    ("run_diagnostics", "payload_json", "jsonb", True),
    ("run_diagnostics", "created_at", "timestamptz", True),
    ("run_diagnostics", "updated_at", "timestamptz", True),
    ("model_token_usage_scopes", "baseline", "text", True),
    ("model_token_usage_scopes", "baseline_json", "jsonb", False),
    ("model_token_usage_scopes", "provider_session_id", "text", False),
    ("model_token_usage_coverage", "coverage", "text", True),
    ("model_token_usage_coverage", "recorded_at", "timestamptz", True),
    ("model_token_usage_scopes", "created_at", "timestamptz", True),
    ("model_token_usage_scopes", "latest_observation_seq", "int8", True),
    ("model_token_usage_scopes", "cumulative_json", "jsonb", True),
    ("model_token_usage_scopes", "coverage", "text", True),
    ("model_token_usage_observations", "observation_seq", "int8", True),
    ("model_token_usage_observations", "payload_json", "jsonb", True),
    ("model_token_usage_observations", "recorded_at", "timestamptz", True),
    ("model_token_usage_observations", "source_receipt_id", "text", True),
    ("model_token_usage_deltas", "observation_seq", "int8", True),
    ("model_token_usage_deltas", "actual_model", "text", True),
    ("model_token_usage_deltas", "input_tokens", "int8", True),
    ("model_token_usage_deltas", "cache_read_input_tokens", "int8", True),
    ("model_token_usage_deltas", "cache_creation_input_tokens", "int8", True),
    ("model_token_usage_deltas", "output_tokens", "int8", True),
    ("model_token_usage_deltas", "recorded_at", "timestamptz", True),
    ("model_gateway_revisions", "revision", "int8", True),
    ("model_gateway_revisions", "base_url", "text", True),
    ("model_gateway_revisions", "api_key_ciphertext", "bytea", True),
    ("model_gateway_revisions", "key_fingerprint", "text", True),
    ("model_gateway_revisions", "active", "bool", True),
    ("model_gateway_revisions", "created_by", "text", True),
    ("model_gateway_revisions", "created_at", "timestamptz", True),
    ("model_catalog_entries", "model_id", "text", True),
    ("model_catalog_entries", "upstream_model_id", "text", True),
    ("model_catalog_entries", "display_name", "text", True),
    ("model_catalog_entries", "provider", "text", True),
    ("model_catalog_entries", "enabled", "bool", True),
    ("model_catalog_entries", "upstream_available", "bool", True),
    ("model_catalog_entries", "is_default", "bool", True),
    ("model_catalog_entries", "display_order", "int4", True),
    ("model_catalog_entries", "auto_compact_window", "int8", False),
    ("model_catalog_entries", "max_output_tokens", "int8", False),
    ("model_catalog_entries", "access_mode", "text", True),
    ("model_catalog_entries", "allowed_user_ids", "jsonb", True),
    ("model_catalog_entries", "first_seen_revision", "int8", True),
    ("model_catalog_entries", "last_seen_revision", "int8", True),
    ("model_catalog_entries", "first_seen_at", "timestamptz", True),
    ("model_catalog_entries", "last_seen_at", "timestamptz", True),
    ("run_attempts", "ordinal", "int4", True),
    ("run_attempts", "status", "text", True),
    ("run_attempts", "owner_kind", "text", True),
    ("run_attempts", "owner_id", "text", True),
    ("run_attempts", "owner_generation", "int8", True),
    ("run_attempts", "queue_attempt_id", "text", True),
    ("run_attempts", "execution_spec_schema_version", "text", True),
    ("run_attempts", "execution_spec_json", "jsonb", True),
    ("run_attempts", "execution_spec_canonical_json", "text", True),
    ("run_attempts", "execution_spec_sha256", "text", True),
    ("run_attempts", "queue_message_id", "text", False),
    ("run_attempts", "lease_expires_at", "timestamptz", False),
    ("run_attempts", "last_heartbeat_at", "timestamptz", False),
    ("run_attempts", "started_at", "timestamptz", False),
    ("run_attempts", "finished_at", "timestamptz", False),
    ("run_attempts", "terminal_reason", "text", True),
    ("run_attempts", "error_code", "text", False),
    ("run_attempts", "created_at", "timestamptz", True),
    ("run_attempts", "updated_at", "timestamptz", True),
    ("run_attempt_settlements", "tenant_id", "text", True),
    ("run_attempt_settlements", "run_id", "text", True),
    ("run_attempt_settlements", "attempt_id", "text", True),
    ("run_attempt_settlements", "schema_version", "text", True),
    ("run_attempt_settlements", "mapping_state", "text", True),
    ("run_attempt_settlements", "source_lease_id", "text", False),
    ("run_attempt_settlements", "receipt_identity", "text", False),
    ("run_attempt_settlements", "receipt_json", "jsonb", False),
    ("run_attempt_settlements", "receipt_sha256", "text", False),
    ("run_attempt_settlements", "receipt_owner_generation", "int8", False),
    ("run_attempt_settlements", "dispatch_state", "text", True),
    ("run_attempt_settlements", "intent_recovery_context_json", "jsonb", False),
    ("run_attempt_settlements", "accepted_context_json", "jsonb", False),
    ("run_attempt_settlements", "attempt_status_snapshot", "text", True),
    ("run_attempt_settlements", "attempt_owner_generation_snapshot", "int8", True),
    ("run_attempt_settlements", "run_status_snapshot", "text", True),
    ("run_attempt_settlements", "cancel_requested_at_snapshot", "timestamptz", False),
    ("run_attempt_settlements", "settlement_phase", "text", True),
    ("run_attempt_settlements", "settlement_claim_token", "text", False),
    ("run_attempt_settlements", "settlement_claimed_at", "timestamptz", False),
    ("run_attempt_settlements", "next_retry_at", "timestamptz", False),
    ("run_attempt_settlements", "settlement_attempt_count", "int4", True),
    ("run_attempt_settlements", "result_status", "text", False),
    ("run_attempt_settlements", "result_json", "jsonb", False),
    ("run_attempt_settlements", "result_sha256", "text", False),
    ("run_attempt_settlements", "settlement_error_code", "text", False),
    ("run_attempt_settlements", "settled_at", "timestamptz", False),
    ("run_attempt_settlements", "created_at", "timestamptz", True),
    ("run_attempt_settlements", "updated_at", "timestamptz", True),
    ("run_attempt_settlement_storage", "singleton", "bool", True),
    ("run_attempt_settlement_storage", "storage_version", "text", True),
    ("run_attempt_settlement_storage", "updated_at", "timestamptz", True),
    ("run_attempt_settlement_writer", "singleton", "bool", True),
    ("run_attempt_settlement_writer", "writer_version", "text", True),
    ("run_attempt_settlement_writer", "updated_at", "timestamptz", True),
    ("run_stale_reconciliation_claims", "tenant_id", "text", True),
    ("run_stale_reconciliation_claims", "run_id", "text", True),
    ("run_stale_reconciliation_claims", "expected_attempt_id", "text", False),
    ("run_stale_reconciliation_claims", "expected_owner_generation", "int8", True),
    ("run_stale_reconciliation_claims", "claim_token", "uuid", True),
    ("run_stale_reconciliation_claims", "claimed_by", "text", True),
    ("run_stale_reconciliation_claims", "claimed_at", "timestamptz", True),
    ("run_stale_reconciliation_claims", "claim_deadline_at", "timestamptz", True),
    ("execution_supervision_domains", "domain", "text", True),
    ("execution_supervision_domains", "mode", "text", True),
    ("execution_supervision_domains", "owner_version", "int8", True),
    ("execution_supervision_domains", "scan_cursor", "text", False),
    ("execution_supervision_domains", "probe_cancel_scan_cursor", "text", False),
    ("execution_supervision_domains", "probe_normal_scan_cursor", "text", False),
    ("execution_supervision_domains", "drain_started_at", "timestamptz", False),
    ("execution_supervision_domains", "updated_at", "timestamptz", True),
    ("execution_supervision_claims", "claim_token", "uuid", True),
    ("execution_supervision_claims", "domain", "text", True),
    ("execution_supervision_claims", "owner_mode", "text", True),
    ("execution_supervision_claims", "worker_id", "text", True),
    ("execution_supervision_claims", "owner_version", "int8", True),
    ("execution_supervision_claims", "expires_at", "timestamptz", True),
    ("execution_supervision_claims", "started_at", "timestamptz", True),
    ("execution_supervision_claims", "recovered_at", "timestamptz", False),
    ("execution_supervision_claims", "recovered_by", "text", False),
    ("execution_supervision_claims", "recovery_evidence", "text", False),
    ("run_input_sessions", "tenant_id", "text", True),
    ("run_input_sessions", "run_id", "text", True),
    ("run_input_sessions", "attempt_id", "text", True),
    ("run_input_sessions", "state", "text", True),
    ("run_input_sessions", "sealed_at", "timestamptz", False),
    ("run_input_questions", "attempt_id", "text", True),
    ("run_input_questions", "question_id", "text", True),
    ("run_input_questions", "questions", "jsonb", True),
    ("run_input_questions", "status", "text", True),
    ("run_inputs", "input_id", "uuid", True),
    ("run_inputs", "attempt_id", "text", True),
    ("run_inputs", "kind", "text", True),
    ("run_inputs", "text", "text", False),
    ("run_inputs", "question_id", "text", False),
    ("run_inputs", "answers", "jsonb", True),
    ("run_inputs", "status", "text", True),
    ("run_inputs", "source", "text", True),
    ("run_skill_materializations", "materialization_sha256", "text", True),
    ("run_skill_materializations", "manifest_json", "jsonb", True),
    ("messages", "content", "text", True),
    ("messages", "metadata_json", "jsonb", True),
    ("files", "storage_key", "text", True),
    ("files", "lifecycle_state", "text", True),
    ("files", "delete_requested_at", "timestamptz", False),
    ("files", "deleted_at", "timestamptz", False),
    ("artifacts", "lifecycle_state", "text", True),
    ("artifacts", "expires_at", "timestamptz", False),
    ("object_deletion_outbox", "target_type", "text", True),
    ("object_deletion_outbox", "artifact_id", "text", False),
    ("object_deletion_outbox", "file_id", "text", False),
    ("object_deletion_outbox", "provider_image_id", "text", False),
    ("object_deletion_outbox", "state", "text", True),
    ("object_deletion_outbox", "lease_generation", "int8", True),
    ("object_deletion_outbox", "dead_letter_at", "timestamptz", False),
    ("object_deletion_outbox", "reconcile_required", "bool", True),
    ("audit_logs", "payload_json", "jsonb", True),
    ("sse_stream_authorities", "attempt_id", "text", True),
    ("sse_stream_authorities", "design_id", "text", True),
    ("sse_stream_authorities", "projection_version", "text", True),
    ("sse_stream_authorities", "tenant_scope", "text", True),
    ("sse_stream_authorities", "stream_incarnation", "int8", True),
    ("sse_stream_authorities", "state", "text", True),
    ("sse_stream_authorities", "open_event_id", "text", True),
    ("sse_stream_authorities", "open_payload_bytes", "text", True),
    ("sse_stream_authorities", "open_payload_digest", "text", True),
    ("sse_stream_authorities", "authorization_epoch", "int8", True),
    ("sse_stream_authorities", "revocation_state", "text", True),
    ("sse_stream_authorities", "admission_created_at", "timestamptz", True),
    ("sse_stream_authorities", "admission_confirmed_at", "timestamptz", False),
    ("sse_stream_authorities", "updated_at", "timestamptz", True),
    ("sandbox_leases", "attempt_id", "text", False),
    ("sandbox_leases", "runtime_container_id", "text", False),
    ("sandbox_leases", "runtime_container_name", "text", False),
    ("sandbox_leases", "runtime_executor_url", "text", False),
    ("sandbox_leases", "runtime_workspace_container_path", "text", False),
    ("sandbox_leases", "runtime_handle_verified_at", "timestamptz", False),
    ("sandbox_leases", "provider_renewed_at", "timestamptz", False),
    ("sandbox_leases", "provider_expires_at", "timestamptz", False),
    ("sandbox_leases", "executor_status", "text", True),
    ("sandbox_leases", "executor_heartbeat_at", "timestamptz", False),
    ("sandbox_leases", "executor_terminal_json", "jsonb", False),
    ("sandbox_leases", "executor_terminal_received_at", "timestamptz", False),
    ("sandbox_leases", "executor_reconciliation_context_json", "jsonb", False),
    ("sandbox_leases", "executor_reconciliation_intent_context_json", "jsonb", False),
    ("sandbox_leases", "executor_reconciliation_status", "text", True),
    ("sandbox_leases", "executor_reconciliation_claim_token", "text", False),
    ("sandbox_leases", "executor_reconciliation_claimed_at", "timestamptz", False),
    ("sandbox_leases", "executor_reconciliation_retry_not_before", "timestamptz", False),
    ("sandbox_leases", "executor_reconciliation_attempt_count", "int4", True),
    (
        "sandbox_leases",
        "executor_terminal_reconciliation_attempt_count",
        "int4",
        True,
    ),
    ("sandbox_leases", "executor_reconciliation_error", "text", True),
    ("sandbox_leases", "executor_reconciled_at", "timestamptz", False),
    ("sandbox_leases", "executor_probe_claim_token", "text", False),
    ("sandbox_leases", "executor_probe_claimed_at", "timestamptz", False),
    ("sandbox_leases", "executor_cleanup_status", "text", True),
    ("sandbox_leases", "executor_cleanup_claim_token", "text", False),
    ("sandbox_leases", "executor_cleanup_claimed_at", "timestamptz", False),
    ("sandbox_leases", "executor_cleanup_retry_not_before", "timestamptz", False),
    ("sandbox_execution_dispatches", "dispatch_id", "text", True),
    ("sandbox_execution_dispatches", "tenant_id", "text", True),
    ("sandbox_execution_dispatches", "run_id", "text", True),
    ("sandbox_execution_dispatches", "attempt_id", "text", True),
    ("sandbox_execution_dispatches", "owner_generation", "int8", True),
    ("sandbox_execution_dispatches", "lease_id", "text", True),
    ("sandbox_execution_dispatches", "provider", "text", True),
    ("sandbox_execution_dispatches", "provider_task_id", "text", True),
    ("sandbox_execution_dispatches", "status", "text", True),
    ("sandbox_execution_dispatches", "created_at", "timestamptz", True),
    ("sandbox_execution_dispatches", "updated_at", "timestamptz", True),
    ("mcp_server_credentials", "credential_envelope", "text", True),
    ("run_context_snapshots", "conversation_authority_json", "jsonb", False),
    ("conversation_context_checkpoints", "id", "text", True),
    ("conversation_context_checkpoints", "tenant_id", "text", True),
    ("conversation_context_checkpoints", "session_id", "text", True),
    ("conversation_context_checkpoints", "source_snapshot_id", "text", True),
    ("conversation_context_checkpoints", "source_sha256", "text", True),
    ("conversation_context_checkpoints", "summary_sha256", "text", False),
    ("conversation_context_checkpoints", "state", "text", True),
    ("conversation_context_checkpoints", "owner_run_id", "text", True),
    ("provider_session_heads", "tenant_id", "text", True),
    ("provider_session_heads", "workspace_id", "text", True),
    ("provider_session_heads", "user_id", "text", True),
    ("provider_session_heads", "session_id", "text", True),
    ("provider_session_heads", "agent_id", "text", True),
    ("provider_session_heads", "engine", "text", True),
    ("provider_session_heads", "current_epoch_id", "text", False),
    ("provider_session_heads", "next_epoch_number", "int8", True),
    ("provider_session_heads", "active_run_id", "text", False),
    ("provider_session_heads", "active_attempt_id", "text", False),
    ("provider_session_epochs", "id", "text", True),
    ("provider_session_epochs", "tenant_id", "text", True),
    ("provider_session_epochs", "session_id", "text", True),
    ("provider_session_epochs", "provider_session_id", "uuid", True),
    ("provider_session_epochs", "next_sequence", "int8", True),
    ("provider_session_epochs", "structured_bytes", "int8", True),
    ("provider_session_epochs", "state", "text", True),
    ("provider_session_epochs", "coverage_source_sha256", "text", False),
    ("provider_session_epochs", "writer_owner_generation", "int8", False),
    ("provider_session_entries", "id", "text", True),
    ("provider_session_entries", "tenant_id", "text", True),
    ("provider_session_entries", "workspace_id", "text", True),
    ("provider_session_entries", "user_id", "text", True),
    ("provider_session_entries", "session_id", "text", True),
    ("provider_session_entries", "agent_id", "text", True),
    ("provider_session_entries", "engine", "text", True),
    ("provider_session_entries", "epoch_id", "text", True),
    ("provider_session_entries", "subpath", "text", True),
    ("provider_session_entries", "sequence", "int8", True),
    ("provider_session_entries", "sdk_entry_uuid", "text", False),
    ("provider_session_entries", "entry_json", "jsonb", True),
    ("provider_session_entries", "entry_canonical_json", "json", False),
    ("provider_session_entries", "image_references", "jsonb", True),
    ("provider_session_entries", "image_format", "text", False),
    ("provider_session_images", "id", "text", True),
    ("provider_session_images", "tenant_id", "text", True),
    ("provider_session_images", "workspace_id", "text", True),
    ("provider_session_images", "user_id", "text", True),
    ("provider_session_images", "session_id", "text", True),
    ("provider_session_images", "agent_id", "text", True),
    ("provider_session_images", "epoch_id", "text", True),
    ("provider_session_images", "run_id", "text", True),
    ("provider_session_images", "attempt_id", "text", True),
    ("provider_session_images", "storage_key", "text", True),
    ("provider_session_images", "sha256", "text", True),
    ("provider_session_images", "lifecycle_state", "text", True),
    ("provider_session_images", "owner_generation", "int8", True),
    ("provider_session_images", "size_bytes", "int8", True),
    ("provider_session_images", "provisional", "bool", True),
    ("provider_session_entries", "created_at", "timestamptz", True),
    ("provider_session_append_receipts", "epoch_id", "text", True),
    ("provider_session_append_receipts", "expected_sequence", "int8", True),
    ("provider_session_append_receipts", "batch_sha256", "text", True),
    ("provider_session_append_receipts", "owner_generation", "int8", True),
    ("provider_turn_receipts", "epoch_id", "text", True),
    ("provider_turn_receipts", "execution_spec_sha256", "text", True),
    ("provider_turn_receipts", "committed_coverage_sha256", "text", False),
    ("run_delivery_intents", "tenant_id", "text", True),
    ("run_delivery_intents", "run_id", "text", True),
    ("run_delivery_intents", "session_id", "text", True),
    ("run_delivery_intents", "source", "text", True),
    ("run_delivery_intents", "submission_id", "uuid", False),
    ("run_delivery_intents", "user_message_id", "text", False),
    ("run_delivery_intents", "queue_payload_json", "text", True),
    ("run_delivery_intents", "queue_message_id", "text", True),
    ("run_delivery_intents", "payload_sha256", "text", True),
    ("run_delivery_intents", "state", "text", True),
    ("run_delivery_intents", "claim_token", "uuid", False),
    ("run_delivery_intents", "claimed_at", "timestamptz", False),
    ("run_delivery_intents", "claim_expires_at", "timestamptz", False),
    ("run_delivery_intents", "attempt_count", "int4", True),
    ("run_delivery_intents", "next_retry_at", "timestamptz", True),
    ("run_delivery_intents", "last_error_code", "text", False),
    ("run_delivery_intents", "queue_position", "int4", False),
    ("run_delivery_intents", "queue_admission_ordinal", "int8", False),
    ("run_delivery_intents", "queue_admission_source", "text", False),
    ("run_delivery_intents", "delivered_at", "timestamptz", False),
    ("run_delivery_intents", "suppressed_at", "timestamptz", False),
    ("run_delivery_intents", "created_at", "timestamptz", True),
    ("run_delivery_intents", "updated_at", "timestamptz", True),
)
CRITICAL_CONSTRAINTS = (
    ("model_token_usage_scopes", "model_token_usage_scopes_pkey"),
    ("model_token_usage_observations", "model_token_usage_observations_pkey"),
    ("model_token_usage_deltas", "model_token_usage_deltas_pkey"),
    ("provider_session_entries", "chk_provider_entry_images"),
    ("provider_session_images", "provider_session_images_pkey"),
    ("provider_session_images", "provider_session_images_storage_key_key"),
    ("provider_session_images", "chk_provider_image_provisional"),
    ("object_deletion_outbox", "object_deletion_outbox_provider_image_id_fkey"),
    ("users", "chk_users_metadata_json_object"),
    ("runs", "fk_runs_model_gateway_revision"),
    ("runs", "chk_runs_model_token_limits"),
    ("model_gateway_revisions", "chk_model_gateway_revision_positive"),
    ("model_gateway_revisions", "chk_model_gateway_base_url"),
    ("model_gateway_revisions", "chk_model_gateway_key_fingerprint"),
    ("model_catalog_entries", "model_catalog_entries_first_seen_revision_fkey"),
    ("model_catalog_entries", "model_catalog_entries_last_seen_revision_fkey"),
    ("model_catalog_entries", "chk_model_catalog_id"),
    ("model_catalog_entries", "chk_model_catalog_upstream_id"),
    ("model_catalog_entries", "chk_model_catalog_display_name"),
    ("model_catalog_entries", "chk_model_catalog_default_enabled"),
    ("model_catalog_entries", "chk_model_catalog_token_limits"),
    ("model_catalog_entries", "chk_model_catalog_access_mode"),
    ("model_catalog_entries", "chk_model_catalog_access_users_array"),
    ("model_catalog_entries", "chk_model_catalog_selected_users_present"),
    ("sessions", "chk_sessions_title_source"),
    ("runs", "fk_runs_workspace_scope"),
    ("runs", "fk_runs_session_scope"),
    ("runs", "chk_runs_execution_skill_identity"),
    ("runs", "chk_runs_status"),
    ("run_diagnostics", "run_diagnostics_pkey"),
    ("run_diagnostics", "fk_run_diagnostics_run"),
    ("run_diagnostics", "chk_run_diagnostics_identity"),
    ("run_diagnostics", "chk_run_diagnostics_revision"),
    ("run_diagnostics", "chk_run_diagnostics_payload"),
    ("run_diagnostics", "run_diagnostics_tenant_id_run_id_key"),
    ("run_delivery_intents", "run_delivery_intents_pkey"),
    ("run_delivery_intents", "fk_run_delivery_intents_run"),
    ("run_delivery_intents", "chk_run_delivery_intents_source"),
    ("run_delivery_intents", "chk_run_delivery_intents_state"),
    ("run_delivery_intents", "chk_run_delivery_intents_identity"),
    ("run_delivery_intents", "chk_run_delivery_intents_attempt_count"),
    ("run_delivery_intents", "chk_run_delivery_intents_claim"),
    ("run_delivery_intents", "chk_run_delivery_intents_receipt"),
    ("run_delivery_intents", "chk_run_delivery_intents_suppressed"),
    ("run_attempts", "fk_run_attempts_run"),
    ("run_attempts", "chk_run_attempts_ordinal"),
    ("run_attempts", "chk_run_attempts_owner_generation"),
    ("run_attempts", "chk_run_attempts_status"),
    ("run_attempts", "chk_run_attempts_owner_kind"),
    ("run_attempts", "chk_run_attempts_required_identity"),
    ("run_attempts", "chk_run_attempts_spec_json"),
    ("run_attempts", "chk_run_attempts_spec_canonical_json"),
    ("run_attempts", "chk_run_attempts_spec_sha256"),
    ("run_attempts", "chk_run_attempts_terminal_time"),
    ("run_attempts", "run_attempts_tenant_id_run_id_ordinal_key"),
    ("run_attempts", "run_attempts_tenant_id_run_id_queue_attempt_id_key"),
    ("run_attempts", "run_attempts_tenant_id_run_id_id_key"),
    ("run_attempt_settlements", "run_attempt_settlements_pkey"),
    ("run_attempt_settlements", "fk_run_attempt_settlements_attempt"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_schema_version"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_mapping_state"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_dispatch_state"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_phase"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_identity"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_receipt"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_claim"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_retry"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_attempt_count"),
    ("run_attempt_settlements", "chk_run_attempt_settlements_result"),
    ("run_attempt_settlement_storage", "run_attempt_settlement_storage_pkey"),
    ("run_attempt_settlement_storage", "chk_run_attempt_settlement_storage_singleton"),
    ("run_attempt_settlement_storage", "chk_run_attempt_settlement_storage_version"),
    ("run_attempt_settlement_writer", "run_attempt_settlement_writer_pkey"),
    ("run_attempt_settlement_writer", "chk_run_attempt_settlement_writer_singleton"),
    ("run_attempt_settlement_writer", "chk_run_attempt_settlement_writer_version"),
    ("run_stale_reconciliation_claims", "run_stale_reconciliation_claims_pkey"),
    ("run_stale_reconciliation_claims", "run_stale_reconciliation_claims_token_key"),
    ("run_stale_reconciliation_claims", "fk_run_stale_reconciliation_claims_run"),
    ("run_stale_reconciliation_claims", "chk_run_stale_reconciliation_claims_identity"),
    ("run_stale_reconciliation_claims", "chk_run_stale_reconciliation_claims_generation"),
    ("run_stale_reconciliation_claims", "chk_run_stale_reconciliation_claims_deadline"),
    ("execution_supervision_domains", "execution_supervision_domains_pkey"),
    ("execution_supervision_claims", "execution_supervision_claims_pkey"),
    ("execution_supervision_claims", "execution_supervision_claims_domain_fkey"),
    ("execution_supervision_claims", "chk_execution_supervision_claim_recovery"),
    ("run_input_sessions", "run_input_sessions_pkey"),
    ("run_input_sessions", "fk_run_input_sessions_run"),
    ("run_input_sessions", "chk_run_input_sessions_identity"),
    ("run_input_sessions", "chk_run_input_sessions_state"),
    ("run_input_sessions", "chk_run_input_sessions_sealed_time"),
    ("run_input_questions", "run_input_questions_pkey"),
    ("run_input_questions", "fk_run_input_questions_session"),
    ("run_input_questions", "chk_run_input_questions_identity"),
    ("run_input_questions", "chk_run_input_questions_payload"),
    ("run_input_questions", "chk_run_input_questions_status"),
    ("run_inputs", "run_inputs_pkey"),
    ("run_inputs", "fk_run_inputs_session"),
    ("run_inputs", "fk_run_inputs_question"),
    ("run_inputs", "chk_run_inputs_kind"),
    ("run_inputs", "chk_run_inputs_status"),
    ("run_inputs", "chk_run_inputs_source"),
    ("run_inputs", "chk_run_inputs_payload"),
    ("sse_stream_authorities", "chk_sse_stream_authority_open_format"),
    ("sse_stream_authorities", "chk_sse_stream_authority_pending_confirmation"),
    ("files", "chk_files_lifecycle_state"),
    ("artifacts", "chk_artifacts_lifecycle_state"),
    ("artifacts", "chk_artifacts_run_owner"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_state"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_target"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_target_state"),
    ("object_deletion_outbox", "object_deletion_outbox_file_id_fkey"),
    ("sandbox_leases", "chk_sandbox_leases_executor_status"),
    ("sandbox_leases", "chk_sandbox_leases_executor_reconciliation_status"),
    ("sandbox_leases", "chk_sandbox_leases_executor_cleanup_status"),
    ("sandbox_execution_dispatches", "sandbox_execution_dispatches_pkey"),
    ("sandbox_execution_dispatches", "sandbox_execution_dispatches_attempt_id_fkey"),
    ("sandbox_execution_dispatches", "sandbox_execution_dispatches_lease_id_fkey"),
    ("sandbox_execution_dispatches", "fk_sandbox_execution_dispatches_run"),
    ("sandbox_execution_dispatches", "uq_sandbox_execution_dispatches_attempt"),
    ("sandbox_execution_dispatches", "sandbox_execution_dispatches_lease_id_key"),
    ("sandbox_execution_dispatches", "chk_sandbox_execution_dispatches_identity"),
    ("sandbox_execution_dispatches", "chk_sandbox_execution_dispatches_status"),
    ("mcp_servers", "mcp_servers_endpoint_not_persisted"),
    ("mcp_tools", "mcp_tools_endpoint_not_persisted"),
    ("conversation_context_checkpoints", "fk_context_checkpoint_session"),
    ("conversation_context_checkpoints", "fk_context_checkpoint_owner_scope"),
    ("conversation_context_checkpoints", "fk_context_checkpoint_source_scope"),
    ("conversation_context_checkpoints", "fk_context_checkpoint_predecessor"),
    ("conversation_context_checkpoints", "chk_context_checkpoint_ready"),
    ("provider_session_heads", "pk_provider_session_heads"),
    ("provider_session_heads", "fk_provider_head_session"),
    ("provider_session_heads", "fk_provider_head_current_epoch"),
    ("provider_session_epochs", "fk_provider_epoch_head"),
    ("provider_session_epochs", "uq_provider_epoch_scope"),
    ("provider_session_epochs", "uq_provider_epoch_number"),
    ("provider_session_epochs", "uq_provider_epoch_provider_id"),
    ("provider_session_entries", "provider_session_entries_pkey"),
    ("provider_session_entries", "fk_provider_entry_epoch"),
    ("provider_session_entries", "uq_provider_entry_global_sequence"),
    ("provider_session_append_receipts", "provider_session_append_receipts_pkey"),
    ("provider_turn_receipts", "fk_provider_turn_epoch"),
)
CRITICAL_TRIGGERS = (
    (
        "run_attempts",
        "trg_run_attempt_heartbeat_monotonicity_guard",
        "ai_platform_guard_run_attempt_heartbeat_monotonicity",
        19,
    ),
    (
        "run_attempts",
        "trg_run_attempt_transition_guard",
        "ai_platform_guard_run_attempt_transition",
        23,
    ),
)
MODEL_CRITICAL_CONSTRAINT_DEFINITIONS = (
    (
        "runs",
        "chk_runs_model_token_limits",
        "c",
        "CHECK (auto_compact_window IS NULL AND max_output_tokens IS NULL OR "
        "auto_compact_window IS NOT NULL AND max_output_tokens IS NOT NULL AND "
        "auto_compact_window >= 100000 AND auto_compact_window <= 1000000 AND "
        "max_output_tokens >= 1 AND max_output_tokens <= 10000000)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_token_limits",
        "c",
        "CHECK (auto_compact_window IS NULL AND max_output_tokens IS NULL OR "
        "auto_compact_window IS NOT NULL AND max_output_tokens IS NOT NULL AND "
        "auto_compact_window >= 100000 AND auto_compact_window <= 1000000 AND "
        "max_output_tokens >= 1 AND max_output_tokens <= 10000000)",
    ),
    (
        "runs",
        "fk_runs_model_gateway_revision",
        "f",
        "FOREIGN KEY (model_gateway_revision) REFERENCES model_gateway_revisions(revision)",
    ),
    (
        "model_gateway_revisions",
        "chk_model_gateway_revision_positive",
        "c",
        "CHECK (revision > 0)",
    ),
    (
        "model_gateway_revisions",
        "chk_model_gateway_base_url",
        "c",
        "CHECK (length(base_url) >= 1 AND length(base_url) <= 2048)",
    ),
    (
        "model_gateway_revisions",
        "chk_model_gateway_key_fingerprint",
        "c",
        "CHECK (key_fingerprint ~ '^[0-9a-f]{16}$'::text)",
    ),
    (
        "model_catalog_entries",
        "model_catalog_entries_first_seen_revision_fkey",
        "f",
        "FOREIGN KEY (first_seen_revision) REFERENCES model_gateway_revisions(revision)",
    ),
    (
        "model_catalog_entries",
        "model_catalog_entries_last_seen_revision_fkey",
        "f",
        "FOREIGN KEY (last_seen_revision) REFERENCES model_gateway_revisions(revision)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_id",
        "c",
        "CHECK (model_id ~ '^[A-Za-z0-9_.:-]{1,128}$'::text)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_upstream_id",
        "c",
        "CHECK (length(upstream_model_id) >= 1 AND length(upstream_model_id) <= 512 "
        "AND upstream_model_id = btrim(upstream_model_id))",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_display_name",
        "c",
        "CHECK (length(display_name) >= 1 AND length(display_name) <= 160)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_default_enabled",
        "c",
        "CHECK (NOT is_default OR enabled)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_access_mode",
        "c",
        "CHECK (access_mode = 'all'::text OR access_mode = 'selected'::text)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_access_users_array",
        "c",
        "CHECK (jsonb_typeof(allowed_user_ids) = 'array'::text)",
    ),
    (
        "model_catalog_entries",
        "chk_model_catalog_selected_users_present",
        "c",
        "CHECK (access_mode <> 'selected'::text OR jsonb_array_length(allowed_user_ids) > 0)",
    ),
)

CRITICAL_CONSTRAINT_DEFINITIONS = (
    (
        "runs",
        "chk_runs_status",
        "c",
        "CHECK (status IS NOT NULL AND (status = ANY (ARRAY["
        "'queued'::text, 'running'::text, 'cancel_requested'::text, "
        "'succeeded'::text, 'failed'::text, 'cancelled'::text, "
        "'canceled'::text, 'dead-letter'::text, 'dead_letter'::text, "
        "'dead-lettered'::text])))",
    ),
    (
        "users",
        "chk_users_metadata_json_object",
        "c",
        "CHECK ((jsonb_typeof(metadata_json) = 'object'::text))",
    ),
    (
        "run_diagnostics",
        "run_diagnostics_pkey",
        "p",
        "PRIMARY KEY (diagnostic_id)",
    ),
    (
        "run_diagnostics",
        "fk_run_diagnostics_run",
        "f",
        "FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id)",
    ),
    (
        "run_diagnostics",
        "chk_run_diagnostics_identity",
        "c",
        "CHECK (diagnostic_id <> ''::text AND tenant_id <> ''::text "
        "AND run_id <> ''::text)",
    ),
    (
        "run_diagnostics",
        "chk_run_diagnostics_revision",
        "c",
        "CHECK (revision > 0)",
    ),
    (
        "run_diagnostics",
        "chk_run_diagnostics_payload",
        "c",
        "CHECK (jsonb_typeof(payload_json) = 'object'::text "
        "AND payload_json ? 'schema_version'::text "
        "AND (payload_json ->> 'schema_version'::text) IS NOT NULL "
        "AND (payload_json ->> 'schema_version'::text) = schema_version "
        "AND octet_length(payload_json::text) <= 147456)",
    ),
    (
        "run_diagnostics",
        "run_diagnostics_tenant_id_run_id_key",
        "u",
        "UNIQUE (tenant_id, run_id)",
    ),
    (
        "run_attempts",
        "run_attempts_tenant_id_run_id_id_key",
        "u",
        "UNIQUE (tenant_id, run_id, id)",
    ),
    (
        "run_attempt_settlements",
        "run_attempt_settlements_pkey",
        "p",
        "PRIMARY KEY (tenant_id, run_id, attempt_id)",
    ),
    (
        "run_attempt_settlements",
        "fk_run_attempt_settlements_attempt",
        "f",
        "FOREIGN KEY (tenant_id, run_id, attempt_id) "
        "REFERENCES run_attempts(tenant_id, run_id, id)",
    ),
    (
        "run_stale_reconciliation_claims",
        "run_stale_reconciliation_claims_pkey",
        "p",
        "PRIMARY KEY (tenant_id, run_id)",
    ),
    (
        "run_stale_reconciliation_claims",
        "run_stale_reconciliation_claims_token_key",
        "u",
        "UNIQUE (claim_token)",
    ),
    (
        "run_stale_reconciliation_claims",
        "fk_run_stale_reconciliation_claims_run",
        "f",
        "FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id) ON DELETE CASCADE",
    ),
    (
        "run_stale_reconciliation_claims",
        "chk_run_stale_reconciliation_claims_deadline",
        "c",
        "CHECK (claim_deadline_at > claimed_at)",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_schema_version",
        "c",
        "CHECK (schema_version = 'ai-platform.run-attempt-settlement.v1'::text)",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_mapping_state",
        "c",
        "CHECK (mapping_state = ANY (ARRAY['mapped'::text, 'unknown'::text, 'conflict'::text]))",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_dispatch_state",
        "c",
        "CHECK (dispatch_state = ANY (ARRAY['unknown'::text, 'accepted'::text, "
        "'receipt_before_acceptance'::text]))",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_phase",
        "c",
        "CHECK (settlement_phase = ANY (ARRAY['awaiting_receipt'::text, "
        "'awaiting_context'::text, 'pending'::text, 'claimed'::text, 'retry'::text, "
        "'settled'::text, 'failed'::text, 'unknown'::text]))",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_identity",
        "c",
        "CHECK (tenant_id <> ''::text AND run_id <> ''::text "
        "AND attempt_id <> ''::text AND attempt_status_snapshot <> ''::text "
        "AND run_status_snapshot <> ''::text "
        "AND attempt_owner_generation_snapshot > 0 "
        "AND (source_lease_id IS NULL OR source_lease_id <> ''::text))",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_receipt",
        "c",
        "CHECK (receipt_identity IS NULL AND receipt_json IS NULL "
        "AND receipt_sha256 IS NULL AND receipt_owner_generation IS NULL "
        "OR receipt_identity IS NOT NULL AND receipt_identity <> ''::text "
        "AND jsonb_typeof(receipt_json) = 'object'::text "
        "AND receipt_sha256 ~ '^[0-9a-f]{64}$'::text "
        "AND receipt_owner_generation > 0)",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_claim",
        "c",
        "CHECK (settlement_phase = 'claimed'::text "
        "AND settlement_claim_token IS NOT NULL "
        "AND settlement_claim_token <> ''::text "
        "AND settlement_claimed_at IS NOT NULL "
        "OR settlement_phase <> 'claimed'::text "
        "AND settlement_claim_token IS NULL AND settlement_claimed_at IS NULL)",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_retry",
        "c",
        "CHECK (settlement_phase = 'retry'::text AND next_retry_at IS NOT NULL "
        "OR settlement_phase <> 'retry'::text AND next_retry_at IS NULL)",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_attempt_count",
        "c",
        "CHECK (settlement_attempt_count >= 0)",
    ),
    (
        "run_attempt_settlements",
        "chk_run_attempt_settlements_result",
        "c",
        "CHECK (result_status IS NULL AND result_json IS NULL "
        "AND result_sha256 IS NULL AND settled_at IS NULL "
        "OR result_status IS NOT NULL AND result_status <> ''::text "
        "AND jsonb_typeof(result_json) = 'object'::text "
        "AND result_sha256 ~ '^[0-9a-f]{64}$'::text "
        "AND settled_at IS NOT NULL)",
    ),
    (
        "run_attempt_settlement_storage",
        "run_attempt_settlement_storage_pkey",
        "p",
        "PRIMARY KEY (singleton)",
    ),
    (
        "run_attempt_settlement_storage",
        "chk_run_attempt_settlement_storage_singleton",
        "c",
        "CHECK (singleton)",
    ),
    (
        "run_attempt_settlement_storage",
        "chk_run_attempt_settlement_storage_version",
        "c",
        "CHECK (storage_version = ANY (ARRAY['sandbox_lease.v1'::text, 'attempt.v1'::text]))",
    ),
    (
        "execution_supervision_domains",
        "execution_supervision_domains_pkey",
        "p",
        "PRIMARY KEY (domain)",
    ),
    (
        "execution_supervision_claims",
        "execution_supervision_claims_pkey",
        "p",
        "PRIMARY KEY (claim_token)",
    ),
    (
        "execution_supervision_claims",
        "execution_supervision_claims_domain_fkey",
        "f",
        "FOREIGN KEY (domain) REFERENCES execution_supervision_domains(domain)",
    ),
    (
        "run_delivery_intents",
        "run_delivery_intents_pkey",
        "p",
        "PRIMARY KEY (tenant_id, run_id)",
    ),
    (
        "run_delivery_intents",
        "fk_run_delivery_intents_run",
        "f",
        "FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id)",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_state",
        "c",
        "CHECK (state = ANY (ARRAY['pending'::text, 'claimed'::text, "
        "'delivered'::text, 'suppressed'::text]))",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_source",
        "c",
        "CHECK (source = ANY (ARRAY['chat'::text, 'create_run'::text, "
        "'copy'::text, 'retry'::text, 'resume'::text]))",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_identity",
        "c",
        "CHECK (session_id <> ''::text AND queue_message_id ~ "
        "'^[0-9a-f]{64}$'::text AND payload_sha256 ~ '^[0-9a-f]{64}$'::text)",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_attempt_count",
        "c",
        "CHECK (attempt_count >= 0)",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_claim",
        "c",
        "CHECK (state = 'claimed'::text AND claim_token IS NOT NULL "
        "AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL "
        "AND claim_expires_at > claimed_at OR state <> 'claimed'::text "
        "AND claim_token IS NULL AND claimed_at IS NULL "
        "AND claim_expires_at IS NULL)",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_receipt",
        "c",
        "CHECK (state = 'delivered'::text AND delivered_at IS NOT NULL "
        "OR state <> 'delivered'::text AND delivered_at IS NULL)",
    ),
    (
        "run_delivery_intents",
        "chk_run_delivery_intents_suppressed",
        "c",
        "CHECK (state = 'suppressed'::text AND suppressed_at IS NOT NULL "
        "OR state <> 'suppressed'::text AND suppressed_at IS NULL)",
    ),
    (
        "mcp_servers",
        "mcp_servers_endpoint_not_persisted",
        "c",
        "CHECK (endpoint_redacted = ''::text)",
    ),
    (
        "mcp_tools",
        "mcp_tools_endpoint_not_persisted",
        "c",
        "CHECK (endpoint = ''::text)",
    ),
    (
        "run_attempts",
        "fk_run_attempts_run",
        "f",
        "FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id)",
    ),
    (
        "run_attempts",
        "chk_run_attempts_ordinal",
        "c",
        "CHECK (ordinal > 0)",
    ),
    (
        "run_attempts",
        "chk_run_attempts_owner_generation",
        "c",
        "CHECK (owner_generation > 0)",
    ),
    (
        "run_attempts",
        "chk_run_attempts_status",
        "c",
        "CHECK (status = ANY (ARRAY["
        "'created'::text, 'queued'::text, 'claimed'::text, 'running'::text, "
        "'cancel_requested'::text, 'expired'::text, 'succeeded'::text, "
        "'failed'::text, 'cancelled'::text]))",
    ),
    (
        "run_attempts",
        "chk_run_attempts_owner_kind",
        "c",
        "CHECK (owner_kind = ANY (ARRAY["
        "'queue_worker'::text, 'reconciler'::text, 'operator'::text]))",
    ),
    (
        "run_attempts",
        "chk_run_attempts_spec_sha256",
        "c",
        "CHECK (execution_spec_sha256 ~ '^[0-9a-f]{64}$'::text "
        "AND execution_spec_sha256 = encode("
        "sha256(convert_to(execution_spec_canonical_json, 'UTF8'::name)), "
        "'hex'::text))",
    ),
    (
        "run_attempts",
        "chk_run_attempts_required_identity",
        "c",
        "CHECK (id <> ''::text AND owner_id <> ''::text "
        "AND queue_attempt_id <> ''::text "
        "AND execution_spec_schema_version <> ''::text "
        "AND (queue_message_id IS NULL OR queue_message_id <> ''::text))",
    ),
    (
        "run_attempts",
        "chk_run_attempts_spec_json",
        "c",
        "CHECK (jsonb_typeof(execution_spec_json) = 'object'::text "
        "AND (execution_spec_json ->> 'schema_version'::text) "
        "= execution_spec_schema_version)",
    ),
    (
        "run_attempts",
        "chk_run_attempts_spec_canonical_json",
        "c",
        "CHECK (execution_spec_canonical_json <> ''::text "
        "AND execution_spec_canonical_json::jsonb = execution_spec_json)",
    ),
    # PostgreSQL renders `NOT (x = ANY (...))` as `x <> ALL (...)` in
    # pg_get_constraintdef(); keep this contract in catalog form.
    (
        "run_attempts",
        "chk_run_attempts_terminal_time",
        "c",
        "CHECK ((status = ANY (ARRAY['succeeded'::text, 'failed'::text, "
        "'cancelled'::text])) AND finished_at IS NOT NULL "
        "OR (status <> ALL (ARRAY['succeeded'::text, 'failed'::text, "
        "'cancelled'::text])) AND finished_at IS NULL)",
    ),
    (
        "run_attempts",
        "run_attempts_tenant_id_run_id_ordinal_key",
        "u",
        "UNIQUE (tenant_id, run_id, ordinal)",
    ),
    (
        "run_attempts",
        "run_attempts_tenant_id_run_id_queue_attempt_id_key",
        "u",
        "UNIQUE (tenant_id, run_id, queue_attempt_id)",
    ),
    (
        "sse_stream_authorities",
        "chk_sse_stream_authority_open_format",
        "c",
        "CHECK (open_event_id <> ''::text AND open_payload_bytes <> ''::text "
        "AND open_payload_digest ~ '^[0-9a-f]{64}$'::text)",
    ),
    (
        "sse_stream_authorities",
        "chk_sse_stream_authority_pending_confirmation",
        "c",
        "CHECK (state = 'admission_pending'::text AND admission_confirmed_at IS NULL "
        "OR state <> 'admission_pending'::text AND admission_confirmed_at IS NOT NULL)",
    ),
    (
        "files",
        "chk_files_lifecycle_state",
        "c",
        "CHECK (lifecycle_state = ANY (ARRAY["
        "'active'::text, 'delete_pending'::text, 'deleted'::text]))",
    ),
    (
        "artifacts",
        "chk_artifacts_lifecycle_state",
        "c",
        "CHECK (lifecycle_state = ANY (ARRAY["
        "'active'::text, 'delete_pending'::text, 'deleted'::text]))",
    ),
    (
        "artifacts",
        "chk_artifacts_run_owner",
        "c",
        "CHECK (run_id IS NOT NULL AND lifecycle_state = 'active'::text OR "
        "run_id IS NULL AND lifecycle_state = 'delete_pending'::text "
        "AND manifest_json @> '{\"provisional_reconciliation_cleanup\": true}'::jsonb "
        "AND NULLIF(manifest_json ->> 'expected_run_id'::text, ''::text) IS NOT NULL OR "
        "run_id IS NULL AND (lifecycle_state = ANY (ARRAY["
        "'delete_pending'::text, 'deleted'::text])) "
        "AND manifest_json @> '{\"retention_artifact_cleanup\": true}'::jsonb "
        "AND NULLIF(manifest_json ->> 'deletion_owner_run_id'::text, ''::text) IS NOT NULL)",
    ),
    (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_state",
        "c",
        "CHECK (state = ANY (ARRAY["
        "'pending'::text, 'processing'::text, 'failed'::text, "
        "'dead_letter'::text, 'deleted'::text, 'file_pending'::text, "
        "'file_processing'::text, 'file_failed'::text, 'file_dead_letter'::text, "
        "'file_deleted'::text, 'provider_image_pending'::text, 'provider_image_processing'::text, "
        "'provider_image_failed'::text, 'provider_image_dead_letter'::text, 'provider_image_deleted'::text]))",
    ),
    (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_target_state",
        "c",
        "CHECK (target_type = 'artifact'::text AND (state = ANY (ARRAY["
        "'pending'::text, 'processing'::text, 'failed'::text, 'dead_letter'::text, "
        "'deleted'::text])) OR target_type = 'provider_image'::text AND (state = ANY (ARRAY["
        "'provider_image_pending'::text, 'provider_image_processing'::text, 'provider_image_failed'::text, "
        "'provider_image_dead_letter'::text, 'provider_image_deleted'::text])) "
        "OR target_type = 'file'::text AND (state = ANY (ARRAY["
        "'file_pending'::text, 'file_processing'::text, 'file_failed'::text, "
        "'file_dead_letter'::text, 'file_deleted'::text])))",
    ),
    (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_target",
        "c",
        "CHECK (target_type = 'artifact'::text AND artifact_id IS NOT NULL "
        "AND file_id IS NULL AND provider_image_id IS NULL "
        "OR target_type = 'file'::text AND artifact_id IS NULL "
        "AND file_id IS NOT NULL AND provider_image_id IS NULL "
        "OR target_type = 'provider_image'::text AND artifact_id IS NULL AND file_id IS NULL "
        "AND provider_image_id IS NOT NULL)",
    ),
    (
        "object_deletion_outbox",
        "object_deletion_outbox_file_id_fkey",
        "f",
        "FOREIGN KEY (file_id) REFERENCES files(id)",
    ),
    (
        "sandbox_leases",
        "chk_sandbox_leases_executor_status",
        "c",
        "CHECK (executor_status = ANY (ARRAY["
        "'pending'::text, 'accepted'::text, 'running'::text, "
        "'completed'::text, 'failed'::text, 'cancelled'::text]))",
    ),
    (
        "sandbox_leases",
        "chk_sandbox_leases_executor_reconciliation_status",
        "c",
        "CHECK (executor_reconciliation_status = ANY (ARRAY["
        "'waiting_terminal'::text, 'pending'::text, 'claimed'::text, "
        "'retry'::text, 'cleanup_pending'::text, 'finalized'::text, 'failed'::text]))",
    ),
    (
        "sandbox_execution_dispatches",
        "sandbox_execution_dispatches_pkey",
        "p",
        "PRIMARY KEY (dispatch_id)",
    ),
    (
        "sandbox_execution_dispatches",
        "sandbox_execution_dispatches_attempt_id_fkey",
        "f",
        "FOREIGN KEY (attempt_id) REFERENCES run_attempts(id)",
    ),
    (
        "sandbox_execution_dispatches",
        "sandbox_execution_dispatches_lease_id_fkey",
        "f",
        "FOREIGN KEY (lease_id) REFERENCES sandbox_leases(id)",
    ),
    (
        "sandbox_execution_dispatches",
        "fk_sandbox_execution_dispatches_run",
        "f",
        "FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, id)",
    ),
    (
        "sandbox_execution_dispatches",
        "uq_sandbox_execution_dispatches_attempt",
        "u",
        "UNIQUE (tenant_id, run_id, attempt_id)",
    ),
    (
        "sandbox_execution_dispatches",
        "sandbox_execution_dispatches_lease_id_key",
        "u",
        "UNIQUE (lease_id)",
    ),
    (
        "sandbox_execution_dispatches",
        "chk_sandbox_execution_dispatches_identity",
        "c",
        "CHECK (dispatch_id <> ''::text AND tenant_id <> ''::text "
        "AND run_id <> ''::text AND attempt_id <> ''::text "
        "AND owner_generation > 0 AND lease_id <> ''::text "
        "AND provider <> ''::text AND provider_task_id <> ''::text)",
    ),
    (
        "sandbox_execution_dispatches",
        "chk_sandbox_execution_dispatches_status",
        "c",
        "CHECK (status = ANY (ARRAY['intent'::text, 'accepted'::text, "
        "'rejected'::text, 'unknown'::text]))",
    ),
)


@dataclass(frozen=True)
class ConcurrentIndexMigration:
    name: str
    sql: str
    table_name: str
    column_names: tuple[str, ...]
    descending: tuple[bool, ...]
    predicate_expression: str = ""
    unique: bool = False
    access_method: str = "btree"
    opclass_names: tuple[str, ...] = ()
    key_expressions: tuple[str, ...] = ()

    @property
    def checksum_sha256(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StaticIndexDefinition:
    """Exact catalog contract for an index installed by the core schema."""

    name: str
    table_name: str
    column_names: tuple[str, ...]
    descending: tuple[bool, ...]
    predicate_expression: str = ""
    unique: bool = False
    access_method: str = "btree"
    opclass_names: tuple[str, ...] = ()
    key_expressions: tuple[str, ...] = ()


CONCURRENT_INDEX_MIGRATIONS = (
    ConcurrentIndexMigration(
        "idx_object_deletion_provider_image_claim",
        "create index concurrently if not exists idx_object_deletion_provider_image_claim "
        "on object_deletion_outbox(state, available_at, created_at, id) "
        "where target_type = 'provider_image'",
        "object_deletion_outbox", ("state", "available_at", "created_at", "id"),
        (False, False, False, False), "target_type = 'provider_image'",
    ),
    ConcurrentIndexMigration(
        "uq_object_deletion_provider_image",
        "create unique index concurrently if not exists uq_object_deletion_provider_image "
        "on object_deletion_outbox(tenant_id, provider_image_id) where provider_image_id is not null",
        "object_deletion_outbox", ("tenant_id", "provider_image_id"),
        (False, False), "provider_image_id is not null", unique=True,
    ),
    ConcurrentIndexMigration(
        'idx_run_events_v4_message_facts',
        "create index concurrently if not exists idx_run_events_v4_message_facts "
        "on run_events(tenant_id, run_id, (payload_json -> '__stream_v4' ->> 'attempt_id'), (payload_json -> '__stream_v4' ->> 'stream_incarnation'), (payload_json -> '__stream_v4' ->> 'message_id'), event_type, sequence)",
        "run_events", ("tenant_id", "run_id", "event_type", "sequence"),
        (False, False, False, False, False, False, False),
        key_expressions=('tenant_id', 'run_id', "payload_json -> '__stream_v4' ->> 'attempt_id'", "payload_json -> '__stream_v4' ->> 'stream_incarnation'", "payload_json -> '__stream_v4' ->> 'message_id'", 'event_type', 'sequence'),
    ),
    ConcurrentIndexMigration(
        'idx_run_events_v4_part_facts',
        "create index concurrently if not exists idx_run_events_v4_part_facts "
        "on run_events(tenant_id, run_id, (payload_json -> '__stream_v4' ->> 'attempt_id'), (payload_json -> '__stream_v4' ->> 'stream_incarnation'), (payload_json ->> 'part_id'), event_type, sequence)",
        "run_events", ("tenant_id", "run_id", "event_type", "sequence"),
        (False, False, False, False, False, False, False),
        key_expressions=('tenant_id', 'run_id', "payload_json -> '__stream_v4' ->> 'attempt_id'", "payload_json -> '__stream_v4' ->> 'stream_incarnation'", "payload_json ->> 'part_id'", 'event_type', 'sequence'),
    ),
    ConcurrentIndexMigration(
        'idx_run_events_v4_source_facts',
        "create index concurrently if not exists idx_run_events_v4_source_facts "
        "on run_events(tenant_id, run_id, (payload_json -> '__stream_v4' ->> 'attempt_id'), (payload_json -> '__stream_v4' ->> 'stream_incarnation'), (payload_json -> '__stream_v4' ->> 'source_event_id'), event_type, sequence)",
        "run_events", ("tenant_id", "run_id", "event_type", "sequence"),
        (False, False, False, False, False, False, False),
        key_expressions=('tenant_id', 'run_id', "payload_json -> '__stream_v4' ->> 'attempt_id'", "payload_json -> '__stream_v4' ->> 'stream_incarnation'", "payload_json -> '__stream_v4' ->> 'source_event_id'", 'event_type', 'sequence'),
    ),
    ConcurrentIndexMigration(
        "idx_messages_tenant_session_created",
        "create index concurrently if not exists idx_messages_tenant_session_created "
        "on messages(tenant_id, session_id, created_at asc, id asc)",
        "messages",
        ("tenant_id", "session_id", "created_at", "id"),
        (False, False, False, False),
    ),
    ConcurrentIndexMigration(
        "idx_files_tenant_owner_session_created",
        "create index concurrently if not exists idx_files_tenant_owner_session_created "
        "on files(tenant_id, workspace_id, user_id, session_id, created_at desc, id desc)",
        "files",
        ("tenant_id", "workspace_id", "user_id", "session_id", "created_at", "id"),
        (False, False, False, False, True, True),
    ),
    ConcurrentIndexMigration(
        "idx_runs_input_json_gin",
        "create index concurrently if not exists idx_runs_input_json_gin "
        "on runs using gin (input_json jsonb_path_ops)",
        "runs",
        ("input_json",),
        (False,),
        access_method="gin",
        opclass_names=("jsonb_path_ops",),
    ),
    ConcurrentIndexMigration(
        "idx_messages_metadata_json_gin",
        "create index concurrently if not exists idx_messages_metadata_json_gin "
        "on messages using gin (metadata_json jsonb_path_ops)",
        "messages",
        ("metadata_json",),
        (False,),
        access_method="gin",
        opclass_names=("jsonb_path_ops",),
    ),
    ConcurrentIndexMigration(
        "idx_run_context_snapshots_file_ids_gin",
        "create index concurrently if not exists idx_run_context_snapshots_file_ids_gin "
        "on run_context_snapshots using gin (included_file_ids)",
        "run_context_snapshots",
        ("included_file_ids",),
        (False,),
        access_method="gin",
        opclass_names=("jsonb_ops",),
    ),
    ConcurrentIndexMigration(
        "idx_artifacts_tenant_run_created",
        "create index concurrently if not exists idx_artifacts_tenant_run_created "
        "on artifacts(tenant_id, run_id, created_at desc, id desc)",
        "artifacts",
        ("tenant_id", "run_id", "created_at", "id"),
        (False, False, True, True),
    ),
    ConcurrentIndexMigration(
        "idx_artifacts_expired_cleanup",
        "create index concurrently if not exists idx_artifacts_expired_cleanup "
        "on artifacts(expires_at asc, created_at asc, id asc) "
        "where lifecycle_state = 'active' and expires_at is not null",
        "artifacts",
        ("expires_at", "created_at", "id"),
        (False, False, False),
        "lifecycle_state = 'active' and expires_at is not null",
    ),
    ConcurrentIndexMigration(
        "idx_artifacts_manifest_json_gin",
        "create index concurrently if not exists idx_artifacts_manifest_json_gin "
        "on artifacts using gin (manifest_json jsonb_path_ops)",
        "artifacts",
        ("manifest_json",),
        (False,),
        access_method="gin",
        opclass_names=("jsonb_path_ops",),
    ),
    ConcurrentIndexMigration(
        "idx_audit_logs_tenant_created",
        "create index concurrently if not exists idx_audit_logs_tenant_created "
        "on audit_logs(tenant_id, created_at desc, id desc)",
        "audit_logs",
        ("tenant_id", "created_at", "id"),
        (False, True, True),
    ),
    ConcurrentIndexMigration(
        "idx_object_deletion_outbox_claim",
        "create index concurrently if not exists idx_object_deletion_outbox_claim "
        "on object_deletion_outbox(state, available_at asc, created_at asc, id asc) "
        "where state = 'pending' or state = 'processing' or state = 'failed' "
        "or state = 'file_pending' or state = 'file_processing' or state = 'file_failed'",
        "object_deletion_outbox",
        ("state", "available_at", "created_at", "id"),
        (False, False, False, False),
        "state = 'pending' or state = 'processing' or state = 'failed' "
        "or state = 'file_pending' or state = 'file_processing' or state = 'file_failed'",
    ),
    ConcurrentIndexMigration(
        "idx_object_deletion_outbox_artifact_storage_live",
        "create index concurrently if not exists "
        "idx_object_deletion_outbox_artifact_storage_live "
        "on object_deletion_outbox(tenant_id, storage_key) "
        "where target_type = 'artifact' and state <> 'deleted'",
        "object_deletion_outbox",
        ("tenant_id", "storage_key"),
        (False, False),
        "target_type = 'artifact' and state <> 'deleted'",
    ),
    ConcurrentIndexMigration(
        "uq_object_deletion_outbox_file",
        "create unique index concurrently if not exists uq_object_deletion_outbox_file "
        "on object_deletion_outbox(tenant_id, file_id) "
        "where target_type = 'file' and file_id is not null",
        "object_deletion_outbox",
        ("tenant_id", "file_id"),
        (False, False),
        "target_type = 'file' and file_id is not null",
        unique=True,
    ),
    ConcurrentIndexMigration(
        "idx_sandbox_leases_executor_reconcile",
        "create index concurrently if not exists idx_sandbox_leases_executor_reconcile "
        "on sandbox_leases((coalesce(executor_terminal_received_at, created_at)), id asc) "
        "where status in ('active', 'released') and executor_terminal_json is not null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status in ('pending', 'retry', 'claimed')",
        "sandbox_leases",
        ("id",),
        (False, False),
        "status = any array['active', 'released'] and executor_terminal_json is not null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status = any array['pending', 'retry', 'claimed']",
        key_expressions=("coalesce(executor_terminal_received_at, created_at)", "id"),
    ),
    ConcurrentIndexMigration(
        "idx_sandbox_leases_executor_reconcile_due",
        "create index concurrently if not exists idx_sandbox_leases_executor_reconcile_due "
        "on sandbox_leases(executor_reconciliation_retry_not_before) "
        "where status in ('active', 'released') and executor_terminal_json is not null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status = 'retry'",
        "sandbox_leases",
        ("executor_reconciliation_retry_not_before",),
        (False,),
        "status = any array['active', 'released'] and executor_terminal_json is not null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status = 'retry'",
    ),
    ConcurrentIndexMigration(
        "idx_sandbox_leases_executor_watch",
        "create index concurrently if not exists idx_sandbox_leases_executor_watch "
        "on sandbox_leases(executor_heartbeat_at asc nulls first, updated_at asc, id asc) "
        "where status = 'active' and executor_terminal_json is null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status in ('waiting_terminal', 'retry', 'claimed')",
        "sandbox_leases",
        ("executor_heartbeat_at", "updated_at", "id"),
        (False, False, False),
        "status = 'active' and executor_terminal_json is null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status = any array['waiting_terminal', 'retry', 'claimed']",
    ),
    ConcurrentIndexMigration(
        "idx_sandbox_leases_executor_probe_due",
        "create index concurrently if not exists idx_sandbox_leases_executor_probe_due "
        "on sandbox_leases(executor_heartbeat_at asc nulls first, updated_at asc, id asc) "
        "where status = 'active' and executor_status in ('accepted', 'running')",
        "sandbox_leases",
        ("executor_heartbeat_at", "updated_at", "id"),
        (False, False, False),
        "status = 'active' and executor_status = any array['accepted', 'running']",
    ),
    ConcurrentIndexMigration(
        "idx_sandbox_leases_executor_cleanup_due",
        "create index concurrently if not exists idx_sandbox_leases_executor_cleanup_due "
        "on sandbox_leases((coalesce(executor_cleanup_claimed_at, executor_terminal_received_at, created_at)), id asc) "
        "where status in ('active', 'released') "
        "and executor_cleanup_status in ('cleanup_pending', 'retry', 'failed', 'claimed')",
        "sandbox_leases",
        ("id",),
        (False,),
        "status = any array['active', 'released'] and executor_cleanup_status = any "
        "array['cleanup_pending', 'retry', 'failed', 'claimed']",
        key_expressions=("coalesce(executor_cleanup_claimed_at, executor_terminal_received_at, created_at)", "id"),
    ),
)
STATIC_INDEX_DEFINITIONS = (
    StaticIndexDefinition(
        "idx_run_stale_reconciliation_claims_deadline",
        "run_stale_reconciliation_claims",
        ("claim_deadline_at", "tenant_id", "run_id"),
        (False, False, False),
    ),
    StaticIndexDefinition(
        "idx_execution_supervision_claims_domain",
        "execution_supervision_claims",
        ("domain", "expires_at", "claim_token"),
        (False, False, False),
    ),
    StaticIndexDefinition(
        "idx_run_delivery_intents_due",
        "run_delivery_intents",
        ("state", "next_retry_at", "created_at", "run_id"),
        (False, False, False, False),
    ),
    StaticIndexDefinition(
        "uq_run_delivery_intents_queue_identity",
        "run_delivery_intents",
        ("queue_message_id",),
        (False,),
        unique=True,
    ),
    StaticIndexDefinition(
        "idx_model_token_usage_coverage_window", "model_token_usage_coverage",
        ("tenant_id", "recorded_at"), (False, False),
    ),
    StaticIndexDefinition(
        "idx_model_token_usage_delta_window", "model_token_usage_deltas",
        ("tenant_id", "recorded_at", "run_id", "actual_model"),
        (False, False, False, False),
    ),
    StaticIndexDefinition(
        "uq_run_events_tenant_run_sequence",
        "run_events",
        ("tenant_id", "run_id", "sequence"),
        (False, False, False),
        unique=True,
    ),
    StaticIndexDefinition(
        "idx_sandbox_leases_attempt",
        "sandbox_leases",
        ("tenant_id", "run_id", "attempt_id", "status"),
        (False, False, False, False),
    ),
    StaticIndexDefinition(
        "uq_run_attempts_one_open",
        "run_attempts",
        ("tenant_id", "run_id"),
        (False, False),
        "status = any array['created', 'queued', 'claimed', 'running', "
        "'cancel_requested', 'expired']",
        unique=True,
    ),
    StaticIndexDefinition(
        "idx_run_attempts_run_created",
        "run_attempts",
        ("tenant_id", "run_id", "ordinal"),
        (False, False, True),
    ),
    StaticIndexDefinition(
        "idx_run_attempts_lease_reconcile",
        "run_attempts",
        ("lease_expires_at", "tenant_id", "run_id", "id"),
        (False, False, False, False),
        "status = any array['claimed', 'running', 'cancel_requested', 'expired']",
    ),
    StaticIndexDefinition(
        "idx_run_attempt_settlements_mapping",
        "run_attempt_settlements",
        ("mapping_state", "tenant_id", "run_id", "attempt_id"),
        (False, False, False, False),
    ),
    StaticIndexDefinition(
        "idx_provider_entry_view",
        "provider_session_entries",
        ("epoch_id", "subpath", "sequence"),
        (False, False, False),
    ),
    StaticIndexDefinition(
        "uq_provider_entry_sdk_uuid",
        "provider_session_entries",
        ("epoch_id", "subpath", "sdk_entry_uuid"),
        (False, False, False),
        "sdk_entry_uuid is not null and sdk_entry_uuid <> ''",
        unique=True,
    ),
    StaticIndexDefinition(
        "idx_sessions_provider_scope",
        "sessions",
        ("tenant_id", "workspace_id", "user_id", "id", "agent_id"),
        (False, False, False, False, False),
        unique=True,
    ),
    StaticIndexDefinition(
        "idx_run_inputs_queued",
        "run_inputs",
        ("tenant_id", "run_id", "attempt_id", "kind", "created_at", "input_id"),
        (False, False, False, False, False, False),
        "status = 'queued'",
    ),
)
CRITICAL_INDEXES = (
    ("uq_model_gateway_active", True),
    ("uq_model_catalog_default", True),
    *((migration.name, migration.unique) for migration in CONCURRENT_INDEX_MIGRATIONS),
    *((definition.name, definition.unique) for definition in STATIC_INDEX_DEFINITIONS),
)


CRITICAL_INDEX_DEFINITIONS = (
    (
        "uq_model_gateway_active",
        "model_gateway_revisions",
        ("active",),
        "active = true",
        True,
    ),
    (
        "uq_model_catalog_default",
        "model_catalog_entries",
        ("is_default",),
        "is_default = true",
        True,
    ),
)


class SchemaMigrationError(RuntimeError):
    """The installed schema cannot be proven compatible with this build."""


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def schema_checksum(sql: str | None = None) -> str:
    core_sql = sql if sql is not None else schema_sql()
    status_constraint_contract = (
        f"{RUN_STATUS_CONSTRAINT_NAME}:CHECK ({RUN_STATUS_CHECK_EXPRESSION})\n"
        f"expected={RUN_STATUS_CHECK_DEFINITION}"
    )
    index_contract = "\n".join(
        f"{migration.name}:{migration.checksum_sha256}" for migration in CONCURRENT_INDEX_MIGRATIONS
    )
    contract = (
        f"{core_sql}\n-- run-status-constraint-contract\n{status_constraint_contract}"
        f"\n-- concurrent-index-contract\n{index_contract}"
    )
    return hashlib.sha256(contract.encode()).hexdigest()


def _critical_trigger_contract() -> tuple[tuple[Any, ...], ...]:
    sql = schema_sql()
    contracts: list[tuple[Any, ...]] = []
    for relation_name, trigger_name, function_name, trigger_type in CRITICAL_TRIGGERS:
        match = re.search(
            rf"create\s+or\s+replace\s+function\s+{re.escape(function_name)}\(\)"
            rf"\s+returns\s+trigger\s+language\s+plpgsql\s+as\s+\$\$(.*?)\$\$;",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            raise SchemaMigrationError(f"schema_trigger_function_missing:{function_name}")
        contracts.append(
            (
                relation_name,
                trigger_name,
                function_name,
                trigger_type,
                match.group(1),
            )
        )
    return tuple(contracts)


async def _ensure_ledger(conn: Any) -> None:
    await conn.execute(
        """
        create table if not exists schema_migrations (
          version text primary key,
          checksum_sha256 text not null,
          state text not null default 'applied',
          applied_at timestamptz not null default now()
        )
        """
    )
    await conn.execute(
        "alter table schema_migrations add column if not exists state text not null default 'applied'"
    )
    await conn.execute(
        """
        create table if not exists schema_index_migrations (
          index_name text primary key,
          target_version text not null,
          checksum_sha256 text not null,
          state text not null check (state in ('building', 'ready', 'failed')),
          attempts integer not null default 0,
          last_error_code text,
          started_at timestamptz,
          completed_at timestamptz,
          updated_at timestamptz not null default now()
        )
        """
    )


def _normalized_constraint_definition(definition: str) -> str:
    without_not_valid = re.sub(r"\s+not\s+valid\s*$", "", definition, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", "", without_not_valid.lower())
    return re.sub(r"^check\(\((.*)\)\)$", r"check(\1)", normalized)


async def _run_status_constraint_state(conn: Any) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select contype::text as constraint_type,
               convalidated as validated,
               pg_get_constraintdef(oid, true) as definition
        from pg_constraint
        where conrelid = to_regclass('runs')
          and conname = %s
        """,
        (RUN_STATUS_CONSTRAINT_NAME,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if (
        str(row.get("constraint_type") or "") != "c"
        or _normalized_constraint_definition(str(row.get("definition") or ""))
        != _normalized_constraint_definition(RUN_STATUS_CHECK_DEFINITION)
    ):
        raise SchemaMigrationError("runs_status_constraint_definition_mismatch")
    return row


async def _install_run_status_constraint(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
) -> bool:
    """Install the check without scanning rows, then commit its short table lock."""

    async with transaction_factory() as conn:
        current = await _run_status_constraint_state(conn)
        if current is not None:
            return False
        await conn.execute(f"SET LOCAL lock_timeout = '{RUN_STATUS_LOCK_TIMEOUT}'")
        await conn.execute(
            f"ALTER TABLE runs ADD CONSTRAINT {RUN_STATUS_CONSTRAINT_NAME} "
            f"CHECK ({RUN_STATUS_CHECK_EXPRESSION}) NOT VALID"
        )
        return True


async def _validate_run_status_constraint(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
) -> bool:
    """Validate in its own transaction with bounded lock acquisition and scan time."""

    async with transaction_factory() as conn:
        current = await _run_status_constraint_state(conn)
        if current is None:
            raise SchemaMigrationError("runs_status_constraint_missing")
        if bool(current.get("validated")):
            return False
        await conn.execute(f"SET LOCAL lock_timeout = '{RUN_STATUS_LOCK_TIMEOUT}'")
        await conn.execute(
            f"SET LOCAL statement_timeout = '{RUN_STATUS_VALIDATION_TIMEOUT}'"
        )
        await conn.execute(
            f"ALTER TABLE runs VALIDATE CONSTRAINT {RUN_STATUS_CONSTRAINT_NAME}"
        )
        return True


async def _ensure_run_status_constraint(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]],
) -> bool:
    """Commit NOT VALID installation before the separately bounded validation scan."""

    installed = await _install_run_status_constraint(
        transaction_factory=transaction_factory,
    )
    validated = await _validate_run_status_constraint(
        transaction_factory=transaction_factory,
    )
    return installed or validated


async def _default_index_connection_factory() -> Any:
    conn = await connect()
    try:
        await conn.set_autocommit(True)
    except BaseException:
        try:
            await conn.close()
        except BaseException:
            pass  # Failed initialization remains the primary error.
        raise
    return conn


async def _acquire_coordinator_lock(conn: Any) -> None:
    """Poll a session lock without leaving a waiter transaction that blocks concurrent index DDL."""

    while True:
        cursor = await conn.execute(
            "select pg_try_advisory_lock(%s) as acquired",
            (INDEX_MIGRATION_LOCK_ID,),
        )
        row = await cursor.fetchone() or {}
        if bool(row.get("acquired")):
            return
        await asyncio.sleep(0.05)


async def _index_is_ready(
    conn: Any,
    migration: ConcurrentIndexMigration | StaticIndexDefinition,
) -> bool:
    cursor = await conn.execute(
        """
        select coalesce(indexes.indisvalid and indexes.indisready, false) as ready,
               coalesce(indexes.indisunique, false) as is_unique,
               relations.relname as table_name,
               access_methods.amname as access_method,
               array(
                 select attributes.attname
                 from unnest(indexes.indkey::smallint[]) with ordinality keys(attnum, position)
                 join pg_attribute attributes
                   on attributes.attrelid = indexes.indrelid
                  and attributes.attnum = keys.attnum
                 where keys.position <= indexes.indnkeyatts
                 order by keys.position
               ) as column_names,
               array(
                 select (options.option_value & 1) = 1
                 from unnest(indexes.indoption::smallint[])
                   with ordinality options(option_value, position)
                 where options.position <= indexes.indnkeyatts
                 order by options.position
               ) as descending,
               array(
                 select opclasses.opcname
                 from unnest(indexes.indclass::oid[])
                   with ordinality classes(opclass_oid, position)
                 join pg_opclass opclasses on opclasses.oid = classes.opclass_oid
                 where classes.position <= indexes.indnkeyatts
                 order by classes.position
               ) as opclass_names,
               array(
                 select pg_get_indexdef(indexes.indexrelid, position, true)
                 from generate_series(1, indexes.indnkeyatts) position
                 order by position
               ) as key_expressions,
               pg_get_expr(indexes.indpred, indexes.indrelid) as predicate
        from pg_index indexes
        join pg_class relations on relations.oid = indexes.indrelid
        join pg_class index_relations on index_relations.oid = indexes.indexrelid
        join pg_am access_methods on access_methods.oid = index_relations.relam
        where indexes.indexrelid = to_regclass(%s)
        """,
        (migration.name,),
    )
    row = await cursor.fetchone()
    if not row or not row.get("ready") or bool(row.get("is_unique")) != migration.unique:
        return False
    if row.get("table_name") != migration.table_name:
        return False
    if row.get("access_method") != migration.access_method:
        return False
    if tuple(row.get("column_names") or ()) != migration.column_names:
        return False
    if tuple(bool(item) for item in row.get("descending") or ()) != migration.descending:
        return False
    if migration.opclass_names and tuple(row.get("opclass_names") or ()) != migration.opclass_names:
        return False
    if migration.key_expressions:
        def normalize_key(value: str) -> str:
            return " ".join(value.lower().replace("::text", "").replace("(", " ").replace(")", " ").split())
        if tuple(normalize_key(value) for value in row.get("key_expressions") or ()) != tuple(
            normalize_key(value) for value in migration.key_expressions
        ):
            return False
    predicate = " ".join(
        str(row.get("predicate") or "")
        .lower()
        .replace("::text", "")
        .replace("(", " ")
        .replace(")", " ")
        .split()
    )
    expected_predicate = " ".join(migration.predicate_expression.lower().split())
    return predicate == expected_predicate


async def _apply_concurrent_indexes(conn: Any) -> bool:
    applied = False
    await _ensure_ledger(conn)
    for migration in CONCURRENT_INDEX_MIGRATIONS:
        cursor = await conn.execute(
            """
            select target_version, checksum_sha256, state
            from schema_index_migrations
            where index_name = %s
            """,
            (migration.name,),
        )
        ledger_row = await cursor.fetchone()
        index_ready = await _index_is_ready(conn, migration)
        if (
            ledger_row is not None
            and ledger_row.get("target_version") == CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION
            and ledger_row.get("checksum_sha256") == migration.checksum_sha256
            and ledger_row.get("state") == "ready"
            and index_ready
        ):
            continue
        await conn.execute(
            """
            insert into schema_index_migrations(
              index_name, target_version, checksum_sha256, state, attempts, started_at,
              completed_at, last_error_code, updated_at
            ) values (%s, %s, %s, 'building', 1, now(), null, null, now())
            on conflict (index_name) do update set
              target_version = excluded.target_version,
              checksum_sha256 = excluded.checksum_sha256,
              state = 'building',
              attempts = schema_index_migrations.attempts + 1,
              started_at = now(),
              completed_at = null,
              last_error_code = null,
              updated_at = now()
            """,
            (
                migration.name,
                CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION,
                migration.checksum_sha256,
            ),
        )
        try:
            if not index_ready:
                await conn.execute(f"drop index concurrently if exists {migration.name}")
            await conn.execute(migration.sql)
            if not await _index_is_ready(conn, migration):
                raise SchemaMigrationError("schema_index_not_valid")
        except Exception as exc:
            await conn.execute(
                """
                update schema_index_migrations
                set state = 'failed', last_error_code = %s, updated_at = now()
                where index_name = %s
                """,
                (type(exc).__name__[:120], migration.name),
            )
            raise SchemaMigrationError(f"schema_index_migration_failed:{migration.name}") from exc
        await conn.execute(
            """
            update schema_index_migrations
            set state = 'ready', completed_at = now(), last_error_code = null, updated_at = now()
            where index_name = %s and target_version = %s and checksum_sha256 = %s
            """,
            (
                migration.name,
                CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION,
                migration.checksum_sha256,
            ),
        )
        applied = True
    cleanup = await conn.execute(
        """
        delete from schema_index_migrations
        where index_name not in (
          select jsonb_array_elements_text(%s::jsonb)
        )
        returning index_name
        """,
        (json.dumps([migration.name for migration in CONCURRENT_INDEX_MIGRATIONS]),),
    )
    if await cleanup.fetchone() is not None:
        applied = True
    return applied






async def _require_no_future_open_attempt_heartbeats(conn: Any) -> None:
    """Block the monotonic guard until clock-poisoned open rows are remediated."""

    contract_cursor = await conn.execute(
        """
        select
          to_regclass('run_attempts') is not null
          and exists (
            select 1
            from pg_attribute
            where attrelid = to_regclass('run_attempts')
              and attname = 'last_heartbeat_at'
              and not attisdropped
          ) as supported
        """
    )
    contract = await contract_cursor.fetchone() or {}
    if not bool(contract.get("supported")):
        return
    future_cursor = await conn.execute(
        """
        select exists (
          select 1
          from run_attempts
          where status in (
            'created', 'queued', 'claimed', 'running',
            'cancel_requested', 'expired'
          )
            and last_heartbeat_at > clock_timestamp() + make_interval(secs => %s)
        ) as blocked
        """,
        (RUN_ATTEMPT_FUTURE_HEARTBEAT_TOLERANCE_SECONDS,),
    )
    future = await future_cursor.fetchone() or {}
    if bool(future.get("blocked")):
        raise SchemaMigrationError(
            "run_attempt_future_heartbeat_requires_remediation"
        )


async def apply_migrations(
    *,
    transaction_factory: Callable[[], AbstractAsyncContextManager[Any]] = transaction,
    index_connection_factory: Callable[[], Awaitable[Any]] = _default_index_connection_factory,
) -> dict[str, object]:
    """Apply versioned core schema, including cutovers, and resumable concurrent indexes."""

    sql = schema_sql()
    checksum = schema_checksum(sql)
    core_applied = False
    schema_migration_completed = False
    coordinator = await index_connection_factory()
    locked = False
    primary_error: BaseException | None = None
    try:
        await _acquire_coordinator_lock(coordinator)
        locked = True
        async with transaction_factory() as conn:
            await conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            await _ensure_ledger(conn)
            cursor = await conn.execute(
                "select checksum_sha256, state from schema_migrations where version = %s",
                (TARGET_SCHEMA_VERSION,),
            )
            row = await cursor.fetchone()
            if row is not None:
                if str(row.get("checksum_sha256") or "") != checksum:
                    raise SchemaMigrationError("schema_migration_checksum_mismatch")
                migration_state = str(row.get("state") or "")
                if migration_state not in {"pending", "applied"}:
                    raise SchemaMigrationError("schema_migration_state_invalid")
            else:
                await _require_no_future_open_attempt_heartbeats(conn)
                await conn.execute(sql)
                await conn.execute(
                    """
                    insert into schema_migrations(version, checksum_sha256, state)
                    values (%s, %s, 'pending')
                    """,
                    (TARGET_SCHEMA_VERSION, checksum),
                )
                migration_state = "pending"
                core_applied = True
        status_constraint_changed = await _ensure_run_status_constraint(
            transaction_factory=transaction_factory,
        )
        if migration_state == "pending":
            async with transaction_factory() as conn:
                await conn.execute(
                    "select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,)
                )
                cursor = await conn.execute(
                    """
                    update schema_migrations
                    set state = 'applied'
                    where version = %s and checksum_sha256 = %s and state = 'pending'
                    returning state
                    """,
                    (TARGET_SCHEMA_VERSION, checksum),
                )
                completed = await cursor.fetchone()
                if not completed or completed.get("state") != "applied":
                    raise SchemaMigrationError("schema_migration_completion_state_missing")
                schema_migration_completed = True
        indexes_applied = await _apply_concurrent_indexes(coordinator)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            if locked:
                await coordinator.execute(
                    "select pg_advisory_unlock(%s)", (INDEX_MIGRATION_LOCK_ID,)
                )
        except BaseException as exc:
            if primary_error is None:
                primary_error = exc
                raise
        finally:
            # Closing the owned session must still be attempted if unlock fails.
            # Preserve the first failure, including migration cancellation.
            try:
                await coordinator.close()
            except BaseException:
                if primary_error is None:
                    raise
    return {
        "status": "applied"
        if core_applied or status_constraint_changed or schema_migration_completed or indexes_applied
        else "current",
        "version": TARGET_SCHEMA_VERSION,
        "checksum_sha256": checksum,
    }


def _json_contract(rows: tuple[tuple[Any, ...], ...], names: tuple[str, ...]) -> str:
    return json.dumps([dict(zip(names, row, strict=True)) for row in rows], separators=(",", ":"))


async def schema_status(conn: Any) -> dict[str, object]:
    checksum = schema_checksum()
    index_ledger_contract = tuple(
        (
            migration.name,
            CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION,
            migration.checksum_sha256,
        )
        for migration in CONCURRENT_INDEX_MIGRATIONS
    )
    relation_cursor = await conn.execute(
        """
        select coalesce(bool_and(to_regclass(relation_name) is not null), false) as current
        from jsonb_array_elements_text(%s::jsonb) relation_name
        """,
        (json.dumps(CRITICAL_RELATIONS),),
    )
    retired_relation_cursor = await conn.execute(
        """
        select coalesce(bool_and(to_regclass(relation_name) is null), true) as current
        from jsonb_array_elements_text(%s::jsonb) as retired(relation_name)
        """,
        (json.dumps(RETIRED_RELATIONS),),
    )
    column_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          attributes.attname is not null
          and types.typname = expected.type_name
          and attributes.attnotnull = expected.not_null
        ), false) as current
        from jsonb_to_recordset(%s::jsonb)
          as expected(relation_name text, column_name text, type_name text, not_null boolean)
        left join pg_attribute attributes
          on attributes.attrelid = to_regclass(expected.relation_name)
         and attributes.attname = expected.column_name
         and attributes.attnum > 0
         and not attributes.attisdropped
        left join pg_type types on types.oid = attributes.atttypid
        """,
        (_json_contract(CRITICAL_COLUMNS, ("relation_name", "column_name", "type_name", "not_null")),),
    )
    constraint_cursor = await conn.execute(
        """
        select coalesce(bool_and(constraints.oid is not null and constraints.convalidated), false) as current
        from jsonb_to_recordset(%s::jsonb) as expected(relation_name text, constraint_name text)
        left join pg_constraint constraints
          on constraints.conrelid = to_regclass(expected.relation_name)
         and constraints.conname = expected.constraint_name
        """,
        (_json_contract(CRITICAL_CONSTRAINTS, ("relation_name", "constraint_name")),),
    )
    constraint_definition_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          constraints.oid is not null
          and constraints.convalidated
          and constraints.contype::text = expected.constraint_type
          and regexp_replace(
            regexp_replace(
              lower(pg_get_constraintdef(constraints.oid, true)), '\\s+', '', 'g'
            ),
            '^check\\(\\((.*)\\)\\)$',
            'check(\\1)'
          ) = regexp_replace(
            regexp_replace(lower(expected.definition), '\\s+', '', 'g'),
            '^check\\(\\((.*)\\)\\)$',
            'check(\\1)'
          )
        ), false) as current
        from jsonb_to_recordset(%s::jsonb)
          as expected(
            relation_name text,
            constraint_name text,
            constraint_type text,
            definition text
          )
        left join pg_constraint constraints
          on constraints.conrelid = to_regclass(expected.relation_name)
         and constraints.conname = expected.constraint_name
        """,
        (
            _json_contract(
                MODEL_CRITICAL_CONSTRAINT_DEFINITIONS + CRITICAL_CONSTRAINT_DEFINITIONS,
                ("relation_name", "constraint_name", "constraint_type", "definition"),
            ),
        ),
    )
    model_index_definition_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          indexes.indexrelid is not null
          and indexes.indisvalid
          and indexes.indisready
          and indexes.indisunique = expected.is_unique
          and relations.relname = expected.relation_name
          and array(
            select attributes.attname::text
            from unnest(indexes.indkey::smallint[]) with ordinality keys(attnum, position)
            join pg_attribute attributes
              on attributes.attrelid = indexes.indrelid
             and attributes.attnum = keys.attnum
            where keys.position <= indexes.indnkeyatts
            order by keys.position
          ) = expected.column_names
          and regexp_replace(
            lower(coalesce(pg_get_expr(indexes.indpred, indexes.indrelid), '')),
            '[[:space:]()]', '', 'g'
          ) = regexp_replace(lower(expected.predicate), '[[:space:]()]', '', 'g')
        ), false) as current
        from jsonb_to_recordset(%s::jsonb) as expected(
          index_name text,
          relation_name text,
          column_names text[],
          predicate text,
          is_unique boolean
        )
        left join pg_index indexes on indexes.indexrelid = to_regclass(expected.index_name)
        left join pg_class relations on relations.oid = indexes.indrelid
        """,
        (
            _json_contract(
                CRITICAL_INDEX_DEFINITIONS,
                ("index_name", "relation_name", "column_names", "predicate", "is_unique"),
            ),
        ),
    )
    trigger_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          triggers.oid is not null
          and not triggers.tgisinternal
          and triggers.tgenabled = 'O'
          and triggers.tgtype::integer = expected.trigger_type
          and triggers.tgqual is null
          and triggers.tgconstraint = 0
          and not triggers.tgdeferrable
          and not triggers.tginitdeferred
          and triggers.tgnargs = 0
          and procedures.proname = expected.function_name
          and procedures.pronamespace = to_regnamespace(current_schema())
          and procedures.prokind = 'f'
          and procedures.pronargs = 0
          and procedures.prorettype = 'trigger'::regtype
          and not procedures.prosecdef
          and not procedures.proleakproof
          and not procedures.proisstrict
          and procedures.provolatile = 'v'
          and procedures.proparallel = 'u'
          and procedures.proconfig is null
          and languages.lanname = 'plpgsql'
          and procedures.prosrc = expected.function_body
        ), false) as current
        from jsonb_to_recordset(%s::jsonb)
          as expected(
            relation_name text,
            trigger_name text,
            function_name text,
            trigger_type integer,
            function_body text
          )
        left join pg_trigger triggers
          on triggers.tgrelid = to_regclass(expected.relation_name)
         and triggers.tgname = expected.trigger_name
        left join pg_proc procedures on procedures.oid = triggers.tgfoid
        left join pg_language languages on languages.oid = procedures.prolang
        """,
        (
            _json_contract(
                _critical_trigger_contract(),
                (
                    "relation_name",
                    "trigger_name",
                    "function_name",
                    "trigger_type",
                    "function_body",
                ),
            ),
        ),
    )
    index_cursor = await conn.execute(
        """
        select coalesce(bool_and(
          indexes.indexrelid is not null
          and indexes.indisvalid
          and indexes.indisready
          and indexes.indisunique = expected.is_unique
        ), false) as current
        from jsonb_to_recordset(%s::jsonb) as expected(index_name text, is_unique boolean)
        left join pg_index indexes on indexes.indexrelid = to_regclass(expected.index_name)
        """,
        (_json_contract(CRITICAL_INDEXES, ("index_name", "is_unique")),),
    )
    ledger_cursor = await conn.execute(
        """
        with expected_indexes as (
          select *
          from jsonb_to_recordset(%s::jsonb)
            as expected(index_name text, target_version text, checksum_sha256 text)
        )
        select
          exists (
            select 1 from schema_migrations
            where version = %s and checksum_sha256 = %s and state = 'applied'
          ) as ledger_current,
          (
            select count(*)
            from expected_indexes expected
            join schema_index_migrations installed
              on installed.index_name = expected.index_name
             and installed.target_version = expected.target_version
             and installed.checksum_sha256 = expected.checksum_sha256
             and installed.state = 'ready'
          ) = (select count(*) from expected_indexes)
          and not exists (
            select 1
            from schema_index_migrations installed
            left join expected_indexes expected
              on expected.index_name = installed.index_name
             and expected.target_version = installed.target_version
             and expected.checksum_sha256 = installed.checksum_sha256
             and installed.state = 'ready'
            where expected.index_name is null
          ) as index_ledger_current
        """,
        (
            _json_contract(
                index_ledger_contract,
                ("index_name", "target_version", "checksum_sha256"),
            ),
            TARGET_SCHEMA_VERSION,
            checksum,
        ),
    )
    relation_row = await relation_cursor.fetchone() or {}
    retired_relation_row = await retired_relation_cursor.fetchone() or {}
    column_row = await column_cursor.fetchone() or {}
    constraint_row = await constraint_cursor.fetchone() or {}
    constraint_definition_row = await constraint_definition_cursor.fetchone() or {}
    model_index_definition_row = await model_index_definition_cursor.fetchone() or {}
    trigger_row = await trigger_cursor.fetchone() or {}
    index_row = await index_cursor.fetchone() or {}
    ledger_row = await ledger_cursor.fetchone() or {}
    relations_current = bool(relation_row.get("current"))
    retired_relations_current = bool(retired_relation_row.get("current"))
    columns_current = bool(column_row.get("current"))
    constraints_current = bool(constraint_row.get("current"))
    constraint_definitions_current = bool(constraint_definition_row.get("current"))
    model_index_definitions_current = bool(model_index_definition_row.get("current"))
    triggers_current = bool(trigger_row.get("current"))
    indexes_current = bool(index_row.get("current"))
    concurrent_index_definitions_current = all(
        [await _index_is_ready(conn, migration) for migration in CONCURRENT_INDEX_MIGRATIONS]
    )
    static_index_definitions_current = all(
        [await _index_is_ready(conn, definition) for definition in STATIC_INDEX_DEFINITIONS]
    )
    contracts_current = all(
        (
            relations_current,
            retired_relations_current,
            columns_current,
            constraints_current,
            constraint_definitions_current,
            model_index_definitions_current,
            triggers_current,
            indexes_current,
            concurrent_index_definitions_current,
            static_index_definitions_current,
        )
    )
    ready = (
        bool(ledger_row.get("ledger_current"))
        and bool(ledger_row.get("index_ledger_current"))
        and contracts_current
    )
    return {
        "ready": ready,
        "target_version": TARGET_SCHEMA_VERSION,
        "checksum_sha256": checksum,
        "ledger_current": bool(ledger_row.get("ledger_current")),
        "index_ledger_current": bool(ledger_row.get("index_ledger_current")),
        "contracts_current": contracts_current,
        "relations_current": relations_current,
        "retired_relations_current": retired_relations_current,
        "columns_current": columns_current,
        "constraints_current": constraints_current,
        "constraint_definitions_current": constraint_definitions_current,
        "model_index_definitions_current": model_index_definitions_current,
        "triggers_current": triggers_current,
        "indexes_current": indexes_current,
        "concurrent_index_definitions_current": concurrent_index_definitions_current,
        "static_index_definitions_current": static_index_definitions_current,
    }


async def require_schema_current() -> dict[str, object]:
    async with transaction() as conn:
        status = await schema_status(conn)
    if not status["ready"]:
        raise SchemaMigrationError("schema_not_current")
    return status


async def require_schema_marker_applied(version: str) -> None:
    """Require an exact historical schema marker before a phased cutover."""
    if not version or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", version):
        raise ValueError("schema_marker_version_invalid")
    async with transaction() as conn:
        cursor = await conn.execute(
            "select state from schema_migrations where version = %s",
            (version,),
        )
        row = await cursor.fetchone()
    if row is None or str(row.get("state") or "") != "applied":
        raise SchemaMigrationError("schema_marker_not_applied")


async def _run_cli(command: str) -> int:
    try:
        if command == "apply":
            result = await apply_migrations()
        else:
            async with transaction() as conn:
                result = await schema_status(conn)
        print(json.dumps(result, sort_keys=True))
        return 0 if command == "apply" or bool(result["ready"]) else 1
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Platform PostgreSQL schema lifecycle")
    parser.add_argument("command", choices=("apply", "status"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_cli(args.command)))


if __name__ == "__main__":
    main()

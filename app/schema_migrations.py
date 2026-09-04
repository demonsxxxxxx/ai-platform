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
EXPERT_SKILL_NAME_SCHEMA_VERSION = "2026.09.03.2"
TARGET_SCHEMA_VERSION = EXPERT_SKILL_NAME_SCHEMA_VERSION
# Concurrent-index authority advances only when its exact index contract changes.
# Keeping this ledger stable preserves readiness for the saved rollback binary.
CONCURRENT_INDEX_LEDGER_SCHEMA_VERSION = RUN_ATTEMPT_RECONCILER_TAKEOVER_SCHEMA_VERSION
RUN_ATTEMPT_FUTURE_HEARTBEAT_TOLERANCE_SECONDS = 5
MIGRATION_LOCK_ID = 7_226_391_831_505_901_103
INDEX_MIGRATION_LOCK_ID = 7_226_391_831_505_901_104
CRITICAL_RELATIONS = (
    "schema_migrations",
    "schema_index_migrations",
    "users",
    "runs",
    "model_gateway_revisions",
    "model_catalog_entries",
    "run_attempts",
    "run_skill_materializations",
    "run_events",
    "agent_profile_favorites",
    "sse_stream_authorities",
    "sse_stream_rebuild_items",
    "messages",
    "files",
    "file_upload_sessions",
    "artifacts",
    "object_deletion_outbox",
    "audit_logs",
    "sandbox_leases",
    "mcp_servers",
    "mcp_server_credentials",
    "mcp_tools",
)
CRITICAL_COLUMNS = (
    ("users", "metadata_json", "jsonb", True),
    ("sessions", "title_source", "text", True),
    ("agent_profile_revisions", "skill_set", "jsonb", True),
    ("agent_profile_revisions", "avatar_seed", "text", True),
    ("agent_profile_revisions", "avatar_style_ref", "text", True),
    ("agent_profile_revisions", "market_tag", "text", True),
    # Temporary physical compatibility for the previous binary; product DTOs ignore it.
    ("agent_profile_revisions", "supported_file_types", "jsonb", True),
    ("runs", "execution_kind", "text", True),
    ("runs", "skill_id", "text", False),
    ("runs", "authz_policy_version", "int4", True),
    ("runs", "authority_source", "text", True),
    ("runs", "authority_checked_at", "timestamptz", False),
    ("runs", "model_id", "text", False),
    ("runs", "model_value", "text", False),
    ("runs", "model_gateway_revision", "int8", False),
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
    ("object_deletion_outbox", "state", "text", True),
    ("object_deletion_outbox", "lease_generation", "int8", True),
    ("object_deletion_outbox", "dead_letter_at", "timestamptz", False),
    ("object_deletion_outbox", "reconcile_required", "bool", True),
    ("audit_logs", "payload_json", "jsonb", True),
    ("run_events", "stream_publication_state", "text", False),
    ("run_events", "stream_publication_attempts", "int4", False),
    ("run_events", "stream_publication_next_attempt_at", "timestamptz", False),
    ("run_events", "stream_publication_redis_id", "text", False),
    ("run_events", "stream_publication_last_error", "text", False),
    ("run_events", "stream_publication_claim_token", "text", False),
    ("run_events", "stream_publication_claim_expires_at", "timestamptz", False),
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
    ("sse_stream_rebuilds", "attempt_id", "text", True),
    ("sse_stream_rebuilds", "source_incarnation", "int8", True),
    ("sse_stream_rebuilds", "source_authorization_epoch", "int8", True),
    ("sse_stream_rebuilds", "origin_incarnation", "int8", True),
    ("sse_stream_rebuilds", "origin_authorization_epoch", "int8", True),
    ("sse_stream_rebuilds", "successor_incarnation", "int8", True),
    ("sse_stream_rebuilds", "successor_authorization_epoch", "int8", True),
    ("sse_stream_rebuilds", "source_authority_fingerprint", "text", True),
    ("sse_stream_rebuilds", "source_cursor_sequence", "int8", True),
    ("sse_stream_rebuilds", "source_through_sequence", "int8", True),
    ("sse_stream_rebuilds", "successor_open_bytes", "text", True),
    ("sse_stream_rebuilds", "claim_token_digest", "text", True),
    ("sse_stream_rebuilds", "claim_expires_at", "timestamptz", True),
    ("sse_stream_rebuilds", "state", "text", True),
    ("sse_stream_rebuilds", "item_count", "int4", True),
    ("sse_stream_rebuilds", "receipt_entry_count", "int4", False),
    ("sse_stream_rebuilds", "receipt_open_event_id", "text", False),
    ("sse_stream_rebuilds", "receipt_terminal_event_id", "text", False),
    ("sse_stream_rebuilds", "receipt_end_event_id", "text", False),
    ("sse_stream_rebuilds", "receipt_last_redis_id", "text", False),
    ("sse_stream_rebuilds", "receipt_last_envelope_bytes", "text", False),
    ("sse_stream_rebuilds", "receipt_last_envelope_digest", "text", False),
    ("sse_stream_rebuilds", "receipt_digest", "text", False),
    ("sse_stream_rebuild_items", "sequence", "int8", True),
    ("sse_stream_rebuild_items", "canonical_envelope_bytes", "text", True),
    ("sse_stream_rebuild_items", "envelope_digest", "text", True),
    ("sse_stream_rebuild_items", "redis_id", "text", False),
    ("sandbox_leases", "attempt_id", "text", False),
    ("sandbox_leases", "runtime_container_id", "text", False),
    ("sandbox_leases", "runtime_container_name", "text", False),
    ("sandbox_leases", "runtime_executor_url", "text", False),
    ("sandbox_leases", "runtime_workspace_container_path", "text", False),
    ("sandbox_leases", "runtime_handle_verified_at", "timestamptz", False),
    ("sandbox_leases", "executor_status", "text", True),
    ("sandbox_leases", "executor_heartbeat_at", "timestamptz", False),
    ("sandbox_leases", "executor_terminal_json", "jsonb", False),
    ("sandbox_leases", "executor_terminal_received_at", "timestamptz", False),
    ("sandbox_leases", "executor_reconciliation_context_json", "jsonb", False),
    ("sandbox_leases", "executor_reconciliation_status", "text", True),
    ("sandbox_leases", "executor_reconciliation_claim_token", "text", False),
    ("sandbox_leases", "executor_reconciliation_claimed_at", "timestamptz", False),
    ("sandbox_leases", "executor_reconciliation_attempt_count", "int4", True),
    (
        "sandbox_leases",
        "executor_terminal_reconciliation_attempt_count",
        "int4",
        True,
    ),
    ("sandbox_leases", "executor_reconciliation_error", "text", True),
    ("sandbox_leases", "executor_reconciled_at", "timestamptz", False),
    ("mcp_server_credentials", "credential_envelope", "text", True),
)
CRITICAL_CONSTRAINTS = (
    ("users", "chk_users_metadata_json_object"),
    ("runs", "fk_runs_model_gateway_revision"),
    ("model_gateway_revisions", "chk_model_gateway_revision_positive"),
    ("model_gateway_revisions", "chk_model_gateway_base_url"),
    ("model_gateway_revisions", "chk_model_gateway_key_fingerprint"),
    ("model_catalog_entries", "model_catalog_entries_first_seen_revision_fkey"),
    ("model_catalog_entries", "model_catalog_entries_last_seen_revision_fkey"),
    ("model_catalog_entries", "chk_model_catalog_id"),
    ("model_catalog_entries", "chk_model_catalog_upstream_id"),
    ("model_catalog_entries", "chk_model_catalog_display_name"),
    ("model_catalog_entries", "chk_model_catalog_default_enabled"),
    ("sessions", "chk_sessions_title_source"),
    ("runs", "fk_runs_workspace_scope"),
    ("runs", "fk_runs_session_scope"),
    ("runs", "chk_runs_execution_skill_identity"),
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
    ("run_events", "chk_run_events_stream_publication_state"),
    ("run_events", "chk_run_events_stream_publication_claim"),
    ("sse_stream_authorities", "chk_sse_stream_authority_open_format"),
    ("sse_stream_authorities", "chk_sse_stream_authority_pending_confirmation"),
    ("sse_stream_rebuilds", "chk_sse_stream_rebuild_identity"),
    ("sse_stream_rebuilds", "chk_sse_stream_rebuild_authority"),
    ("sse_stream_rebuilds", "chk_sse_stream_rebuild_origin"),
    ("sse_stream_rebuilds", "chk_sse_stream_rebuild_progress"),
    ("sse_stream_rebuilds", "chk_sse_stream_rebuild_state"),
    ("sse_stream_rebuilds", "chk_sse_stream_rebuild_receipt"),
    ("sse_stream_rebuilds", "fk_sse_stream_rebuild_authority"),
    ("sse_stream_rebuild_items", "sse_stream_rebuild_items_pkey"),
    ("sse_stream_rebuild_items", "chk_sse_stream_rebuild_item"),
    ("sse_stream_rebuild_items", "chk_sse_stream_rebuild_item_redis_id"),
    ("sse_stream_rebuild_items", "fk_sse_stream_rebuild_item_operation"),
    ("sse_stream_rebuild_items", "uq_sse_stream_rebuild_item_event"),
    ("files", "chk_files_lifecycle_state"),
    ("artifacts", "chk_artifacts_lifecycle_state"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_state"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_target"),
    ("object_deletion_outbox", "chk_object_deletion_outbox_target_state"),
    ("object_deletion_outbox", "object_deletion_outbox_file_id_fkey"),
    ("sandbox_leases", "chk_sandbox_leases_executor_status"),
    ("sandbox_leases", "chk_sandbox_leases_executor_reconciliation_status"),
    ("mcp_servers", "mcp_servers_endpoint_not_persisted"),
    ("mcp_tools", "mcp_tools_endpoint_not_persisted"),
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
    (
        "agent_profile_revisions",
        "trg_agent_profile_legacy_insert_compatibility",
        "agent_profile_legacy_insert_compatibility",
        7,
    ),
    (
        "agent_profile_revisions",
        "trg_agent_profile_legacy_insert_reconcile",
        "agent_profile_legacy_insert_reconcile",
        5,
    ),
)
MODEL_CRITICAL_CONSTRAINT_DEFINITIONS = (
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
)

CRITICAL_CONSTRAINT_DEFINITIONS = (
    (
        "users",
        "chk_users_metadata_json_object",
        "c",
        "CHECK ((jsonb_typeof(metadata_json) = 'object'::text))",
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
        "run_events",
        "chk_run_events_stream_publication_state",
        "c",
        "CHECK (stream_publication_state IS NULL OR (stream_publication_state = ANY (ARRAY["
        "'pending'::text, 'published'::text, 'suppressed'::text])))",
    ),
    (
        "run_events",
        "chk_run_events_stream_publication_claim",
        "c",
        "CHECK (stream_publication_claim_token IS NULL AND "
        "stream_publication_claim_expires_at IS NULL OR "
        "stream_publication_claim_token IS NOT NULL AND "
        "stream_publication_claim_expires_at IS NOT NULL)",
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
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_identity",
        "c",
        "CHECK (id <> ''::text AND attempt_id <> ''::text "
        "AND successor_open_event_id <> ''::text AND successor_open_bytes <> ''::text "
        "AND source_authority_fingerprint ~ '^[0-9a-f]{64}$'::text "
        "AND successor_open_digest ~ '^[0-9a-f]{64}$'::text "
        "AND claim_token_digest ~ '^[0-9a-f]{64}$'::text)",
    ),
    (
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_authority",
        "c",
        "CHECK (source_incarnation > 0 AND successor_incarnation > source_incarnation "
        "AND source_authorization_epoch > 0 "
        "AND successor_authorization_epoch > source_authorization_epoch)",
    ),
    (
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_origin",
        "c",
        "CHECK (origin_incarnation > 0 AND origin_incarnation <= source_incarnation "
        "AND origin_authorization_epoch > 0 "
        "AND origin_authorization_epoch <= source_authorization_epoch)",
    ),
    (
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_progress",
        "c",
        "CHECK (source_cursor_sequence >= source_through_sequence "
        "AND source_through_sequence > 0 AND item_count > 0 "
        "AND built_through_sequence >= 0 "
        "AND built_through_sequence <= source_through_sequence)",
    ),
    (
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_state",
        "c",
        "CHECK (state = ANY (ARRAY['building'::text, 'ready'::text, "
        "'cutover'::text, 'aborted'::text, 'expired'::text]))",
    ),
    (
        "sse_stream_rebuilds",
        "chk_sse_stream_rebuild_receipt",
        "c",
        "CHECK (receipt_entry_count IS NULL AND receipt_open_event_id IS NULL "
        "AND receipt_terminal_event_id IS NULL AND receipt_end_event_id IS NULL "
        "AND receipt_last_redis_id IS NULL AND receipt_last_envelope_bytes IS NULL "
        "AND receipt_last_envelope_digest IS NULL AND receipt_digest IS NULL "
        "OR receipt_entry_count IS NOT NULL "
        "AND receipt_entry_count = (item_count + 2) "
        "AND receipt_open_event_id IS NOT NULL AND receipt_open_event_id <> ''::text "
        "AND receipt_terminal_event_id IS NOT NULL "
        "AND receipt_terminal_event_id <> ''::text "
        "AND receipt_end_event_id IS NOT NULL AND receipt_end_event_id <> ''::text "
        "AND receipt_last_redis_id IS NOT NULL "
        "AND receipt_last_redis_id ~ '^[0-9]+-[0-9]+$'::text "
        "AND receipt_last_envelope_bytes IS NOT NULL "
        "AND receipt_last_envelope_bytes <> ''::text "
        "AND receipt_last_envelope_digest IS NOT NULL "
        "AND receipt_last_envelope_digest ~ '^[0-9a-f]{64}$'::text "
        "AND receipt_digest IS NOT NULL "
        "AND receipt_digest ~ '^[0-9a-f]{64}$'::text)",
    ),
    (
        "sse_stream_rebuilds",
        "fk_sse_stream_rebuild_authority",
        "f",
        "FOREIGN KEY (tenant_id, run_id) "
        "REFERENCES sse_stream_authorities(tenant_id, run_id)",
    ),
    (
        "sse_stream_rebuild_items",
        "sse_stream_rebuild_items_pkey",
        "p",
        "PRIMARY KEY (rebuild_id, sequence)",
    ),
    (
        "sse_stream_rebuild_items",
        "chk_sse_stream_rebuild_item",
        "c",
        "CHECK (sequence > 0 AND event_id <> ''::text AND event_type <> ''::text "
        "AND canonical_envelope_bytes <> ''::text "
        "AND envelope_digest ~ '^[0-9a-f]{64}$'::text)",
    ),
    (
        "sse_stream_rebuild_items",
        "chk_sse_stream_rebuild_item_redis_id",
        "c",
        "CHECK (redis_id IS NULL OR redis_id ~ '^[0-9]+-[0-9]+$'::text)",
    ),
    (
        "sse_stream_rebuild_items",
        "fk_sse_stream_rebuild_item_operation",
        "f",
        "FOREIGN KEY (rebuild_id) REFERENCES sse_stream_rebuilds(id)",
    ),
    (
        "sse_stream_rebuild_items",
        "uq_sse_stream_rebuild_item_event",
        "u",
        "UNIQUE (rebuild_id, event_id)",
    ),
    (
        "files",
        "chk_files_lifecycle_state",
        "c",
        "CHECK (lifecycle_state = ANY (ARRAY["
        "'active'::text, 'delete_pending'::text, 'deleted'::text]))",
    ),
    (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_state",
        "c",
        "CHECK (state = ANY (ARRAY["
        "'pending'::text, 'processing'::text, 'failed'::text, "
        "'dead_letter'::text, 'deleted'::text, 'file_pending'::text, "
        "'file_processing'::text, 'file_failed'::text, 'file_dead_letter'::text, "
        "'file_deleted'::text]))",
    ),
    (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_target_state",
        "c",
        "CHECK (target_type = 'artifact'::text AND (state = ANY (ARRAY["
        "'pending'::text, 'processing'::text, 'failed'::text, 'dead_letter'::text, "
        "'deleted'::text])) OR target_type = 'file'::text AND (state = ANY (ARRAY["
        "'file_pending'::text, 'file_processing'::text, 'file_failed'::text, "
        "'file_dead_letter'::text, 'file_deleted'::text])))",
    ),
    (
        "object_deletion_outbox",
        "chk_object_deletion_outbox_target",
        "c",
        "CHECK (target_type = 'artifact'::text AND artifact_id IS NOT NULL "
        "AND file_id IS NULL OR target_type = 'file'::text AND artifact_id IS NULL "
        "AND file_id IS NOT NULL)",
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
        "'retry'::text, 'finalized'::text, 'failed'::text]))",
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


CONCURRENT_INDEX_MIGRATIONS = (
    ConcurrentIndexMigration(
        "idx_run_events_stream_publication_retry",
        "create index concurrently if not exists idx_run_events_stream_publication_retry "
        "on run_events(stream_publication_next_attempt_at asc, created_at asc, id asc) "
        "where visible_to_user = true and stream_publication_state = 'pending'",
        "run_events",
        ("stream_publication_next_attempt_at", "created_at", "id"),
        (False, False, False),
        "visible_to_user = true and stream_publication_state = 'pending'",
    ),
    ConcurrentIndexMigration(
        "idx_run_events_stream_publication_claim",
        "create index concurrently if not exists idx_run_events_stream_publication_claim "
        "on run_events(tenant_id, run_id, sequence asc, id asc) "
        "where visible_to_user = true and stream_publication_state = 'pending' "
        "and payload_json ? '__stream_v4'",
        "run_events",
        ("tenant_id", "run_id", "sequence", "id"),
        (False, False, False, False),
        "visible_to_user = true and stream_publication_state = 'pending' "
        "and payload_json ? '__stream_v4'",
    ),
    ConcurrentIndexMigration(
        "idx_run_events_v4_due_scope",
        "create index concurrently if not exists idx_run_events_v4_due_scope "
        "on run_events(tenant_id, run_id, sequence asc) "
        "where visible_to_user = true and payload_json ? '__stream_v4' "
        "and stream_publication_state = 'pending'",
        "run_events",
        ("tenant_id", "run_id", "sequence"),
        (False, False, False),
        "visible_to_user = true and payload_json ? '__stream_v4' "
        "and stream_publication_state = 'pending'",
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
        "on sandbox_leases(executor_reconciliation_status, updated_at asc, id asc) "
        "where status = 'active' and executor_terminal_json is not null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status in ('pending', 'retry', 'claimed')",
        "sandbox_leases",
        ("executor_reconciliation_status", "updated_at", "id"),
        (False, False, False),
        "status = 'active' and executor_terminal_json is not null "
        "and executor_reconciliation_context_json is not null "
        "and executor_reconciliation_status = any array['pending', 'retry', 'claimed']",
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
)
STATIC_INDEX_DEFINITIONS = (
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
        "idx_sse_stream_authority_pending",
        "sse_stream_authorities",
        ("state", "updated_at", "tenant_id", "run_id"),
        (False, False, False, False),
        "state = 'admission_pending'",
    ),
    StaticIndexDefinition(
        "uq_sse_stream_rebuild_successor",
        "sse_stream_rebuilds",
        ("tenant_id", "run_id", "successor_incarnation"),
        (False, False, False),
        unique=True,
    ),
    StaticIndexDefinition(
        "uq_sse_stream_rebuild_active",
        "sse_stream_rebuilds",
        ("tenant_id", "run_id"),
        (False, False),
        "state = any array['building', 'ready']",
        unique=True,
    ),
    StaticIndexDefinition(
        "idx_sse_stream_rebuild_claim_expiry",
        "sse_stream_rebuilds",
        ("state", "claim_expires_at", "tenant_id", "run_id"),
        (False, False, False, False),
        "state = any array['building', 'ready']",
    ),
    StaticIndexDefinition(
        "uq_sse_stream_rebuild_item_event",
        "sse_stream_rebuild_items",
        ("rebuild_id", "event_id"),
        (False, False),
        unique=True,
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
    index_contract = "\n".join(
        f"{migration.name}:{migration.checksum_sha256}" for migration in CONCURRENT_INDEX_MIGRATIONS
    )
    return hashlib.sha256(f"{core_sql}\n-- concurrent-index-contract\n{index_contract}".encode()).hexdigest()


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
          applied_at timestamptz not null default now()
        )
        """
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


async def _default_index_connection_factory() -> Any:
    conn = await connect()
    await conn.set_autocommit(True)
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


async def rollback_v4_successor_rebuild_migration(conn: Any) -> None:
    """Remove dormant successor snapshots, but never activated lineage."""

    activated = await conn.execute(
        "select 1 from sse_stream_rebuilds where state = 'cutover' limit 1"
    )
    if await activated.fetchone() is not None:
        raise SchemaMigrationError("v4_successor_rebuild_rollback_cutover_exists")
    await conn.execute("drop table if exists sse_stream_rebuild_items")
    await conn.execute("drop table if exists sse_stream_rebuilds")
    await conn.execute("drop index if exists idx_run_events_v4_due_scope")
    await conn.execute(
        "delete from schema_index_migrations where index_name = %s",
        ("idx_run_events_v4_due_scope",),
    )
    await conn.execute(
        "delete from schema_migrations where version in (%s, %s, %s)",
        (
            V4_SUCCESSOR_REBUILD_SCHEMA_VERSION,
            V4_SUCCESSOR_ACTIVATION_SCHEMA_VERSION,
            V4_CONCURRENT_DUE_INDEX_SCHEMA_VERSION,
        ),
    )


async def rollback_v4_publication_migration(conn: Any) -> None:
    """Remove only additive publication bookkeeping; event facts stay intact."""

    await conn.execute("drop index if exists idx_run_events_stream_publication_claim")
    await conn.execute("drop index if exists idx_run_events_stream_publication_retry")
    await conn.execute("drop index if exists idx_run_events_v4_due_scope")
    await conn.execute(
        "delete from schema_index_migrations where index_name in (%s, %s, %s)",
        (
            "idx_run_events_stream_publication_claim",
            "idx_run_events_stream_publication_retry",
            "idx_run_events_v4_due_scope",
        ),
    )
    await conn.execute(
        "delete from schema_migrations where version in (%s, %s)",
        (V4_PUBLICATION_SCHEMA_VERSION, V4_CONCURRENT_DUE_INDEX_SCHEMA_VERSION),
    )
    await conn.execute(
        "alter table run_events drop constraint if exists chk_run_events_stream_publication_claim"
    )
    await conn.execute(
        "alter table run_events drop constraint if exists chk_run_events_stream_publication_state"
    )
    await conn.execute(
        """
        alter table run_events
          drop column if exists stream_publication_claim_token,
          drop column if exists stream_publication_claim_expires_at,
          drop column if exists stream_publication_state,
          drop column if exists stream_publication_attempts,
          drop column if exists stream_publication_next_attempt_at,
          drop column if exists stream_publication_redis_id,
          drop column if exists stream_publication_last_error
        """
    )


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
    """Apply additive core schema and resumable concurrent indexes."""

    sql = schema_sql()
    checksum = schema_checksum(sql)
    core_applied = False
    coordinator = await index_connection_factory()
    locked = False
    try:
        await _acquire_coordinator_lock(coordinator)
        locked = True
        async with transaction_factory() as conn:
            await conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            await _ensure_ledger(conn)
            cursor = await conn.execute(
                "select checksum_sha256 from schema_migrations where version = %s",
                (TARGET_SCHEMA_VERSION,),
            )
            row = await cursor.fetchone()
            if row is not None:
                if str(row.get("checksum_sha256") or "") != checksum:
                    raise SchemaMigrationError("schema_migration_checksum_mismatch")
            else:
                await _require_no_future_open_attempt_heartbeats(conn)
                await conn.execute(sql)
                await conn.execute(
                    """
                    insert into schema_migrations(version, checksum_sha256)
                    values (%s, %s)
                    """,
                    (TARGET_SCHEMA_VERSION, checksum),
                )
                core_applied = True
        indexes_applied = await _apply_concurrent_indexes(coordinator)
    finally:
        if locked:
            await coordinator.execute("select pg_advisory_unlock(%s)", (INDEX_MIGRATION_LOCK_ID,))
        await coordinator.close()
    return {
        "status": "applied" if core_applied or indexes_applied else "current",
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
            where version = %s and checksum_sha256 = %s
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
    column_row = await column_cursor.fetchone() or {}
    constraint_row = await constraint_cursor.fetchone() or {}
    constraint_definition_row = await constraint_definition_cursor.fetchone() or {}
    model_index_definition_row = await model_index_definition_cursor.fetchone() or {}
    trigger_row = await trigger_cursor.fetchone() or {}
    index_row = await index_cursor.fetchone() or {}
    ledger_row = await ledger_cursor.fetchone() or {}
    relations_current = bool(relation_row.get("current"))
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

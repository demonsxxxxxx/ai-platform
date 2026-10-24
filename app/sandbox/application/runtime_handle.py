"""Recover only a verified persisted handle under the original provider policies."""

from collections.abc import Callable
from typing import Any, Protocol


class GovernedCleanupPolicy(Protocol):
    GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID: str
    GOVERNED_EGRESS_PROOF_LABEL: str

    def governed_egress_previous_signing_keys(self, value: str) -> Any: ...
    def governed_egress_proof_label(self, proof: object) -> str: ...
    def is_governed_egress_proof(self, proof: object, **kwargs: Any) -> bool: ...


class OpenSandboxCleanupPolicy(Protocol):
    SANDBOX_SECURITY_PROFILE_GOVERNED: str
    SANDBOX_SECURITY_PROFILE_INTERNAL_TEST: str
    SANDBOX_SECURITY_PROFILE_LABEL: str

    def historical_internal_test_cleanup_expected_labels(self, lease: Any) -> Any: ...
    def active_internal_test_lease_is_authorized(self, lease: Any, settings: Any) -> bool: ...


class PersistedRuntimeHandleRecovery:
    def __init__(
        self,
        *,
        lease_factory: Callable[..., Any],
        assert_safe_id: Callable[[Any, str], str],
        settings: Callable[[], Any],
        egress_policy: GovernedCleanupPolicy,
        opensandbox_policy: OpenSandboxCleanupPolicy,
    ) -> None:
        self._lease_factory = lease_factory
        self._assert_safe_id = assert_safe_id
        self._settings = settings
        self._egress_policy = egress_policy
        self._opensandbox_policy = opensandbox_policy

    def from_persisted_row(
        self,
        row: dict[str, Any],
        *,
        allow_historical_internal_test_cleanup: bool = False,
    ) -> Any | None:
        provider = str(row.get("provider") or "fake")
        if provider not in {"fake", "docker", "opensandbox"}:
            return None
        run_id = str(row["run_id"])
        container_id = str(row.get("runtime_container_id") or "").strip()
        container_name = str(row.get("runtime_container_name") or "").strip()
        executor_url = str(row.get("runtime_executor_url") or "").strip()
        workspace_container_path = str(row.get("runtime_workspace_container_path") or "").strip()
        if not (
            container_id
            and container_name
            and executor_url
            and workspace_container_path
            and row.get("runtime_handle_verified_at")
        ):
            return None
        labels: dict[str, str] = {}
        historical_internal_test = False
        if provider == "docker":
            lease_payload = row.get("lease_payload_json")
            if not isinstance(lease_payload, dict):
                lease_payload = row.get("lease_payload")
            lease_payload = lease_payload if isinstance(lease_payload, dict) else {}
            attempt_id = row.get("attempt_id")
            if attempt_id is None:
                attempt_id = lease_payload.get("attempt_id")
            persisted_labels = lease_payload.get("labels")
            persisted_labels = persisted_labels if isinstance(persisted_labels, dict) else {}
            if attempt_id is None and persisted_labels.get("ai-platform.attempt_id") is not None:
                return None
            if attempt_id is not None:
                try:
                    labels["ai-platform.attempt_id"] = self._assert_safe_id(attempt_id, "attempt_id")
                except (TypeError, ValueError):
                    return None
                if any(
                    value is not None and value != attempt_id
                    for value in (
                        lease_payload.get("attempt_id"),
                        persisted_labels.get("ai-platform.attempt_id"),
                    )
                ):
                    return None
            native_required = persisted_labels.get("ai-platform.native_tool_required")
            if native_required is not None:
                if native_required not in ("true", "false") or (native_required == "true" and not attempt_id):
                    return None
                labels["ai-platform.native_tool_required"] = native_required
            proof = lease_payload.get("governed_egress_proof")
            if proof is not None:
                try:
                    labels[self._egress_policy.GOVERNED_EGRESS_PROOF_LABEL] = (
                        self._egress_policy.governed_egress_proof_label(proof)
                    )
                except (TypeError, ValueError):
                    # Cleanup remains possible for historical rows whose proof
                    # was never persisted or is no longer usable for control.
                    pass
        if provider == "opensandbox":
            lease_payload = row.get("lease_payload_json")
            if not isinstance(lease_payload, dict):
                lease_payload = row.get("lease_payload")
            if not isinstance(lease_payload, dict):
                return None
            security_profile = lease_payload.get("security_profile")
            if not isinstance(security_profile, str) or not security_profile:
                persisted_labels = lease_payload.get("labels")
                security_profile = (
                    str(persisted_labels.get(self._opensandbox_policy.SANDBOX_SECURITY_PROFILE_LABEL) or "")
                    if isinstance(persisted_labels, dict)
                    else ""
                ) or self._opensandbox_policy.SANDBOX_SECURITY_PROFILE_GOVERNED
            if security_profile == self._opensandbox_policy.SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
                if (
                    lease_payload.get("internal_test_lease_version") != "active-v1"
                    and not allow_historical_internal_test_cleanup
                ):
                    return None
                if lease_payload.get("internal_test_lease_version") not in (None, "active-v1"):
                    return None
                persisted = lease_payload.get("labels")
                if not isinstance(persisted, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in persisted.items()
                ):
                    return None
                try:
                    attempt_id = self._assert_safe_id(str(lease_payload.get("attempt_id") or ""), "attempt_id")
                except ValueError:
                    return None
                if row.get("attempt_id") is not None and row.get("attempt_id") != attempt_id:
                    return None
                if (
                    lease_payload.get("container_id") != container_id
                    or lease_payload.get("container_name") != container_name
                    or lease_payload.get("executor_url") != executor_url
                    or lease_payload.get("workspace_container_path") != workspace_container_path
                    or lease_payload.get("requested_image")
                    != persisted.get("ai-platform.executor.requested_image")
                    or lease_payload.get("requested_image_digest")
                    != persisted.get("ai-platform.executor.requested_image_digest")
                ):
                    return None
                labels.update(persisted)
                historical_internal_test = True
            elif security_profile != self._opensandbox_policy.SANDBOX_SECURITY_PROFILE_GOVERNED:
                return None
            else:
                attempt_id = lease_payload.get("attempt_id")
                if not isinstance(attempt_id, str):
                    return None
                proof = lease_payload.get("governed_egress_proof")
                try:
                    labels["ai-platform.attempt_id"] = self._assert_safe_id(attempt_id, "attempt_id")
                    labels[self._egress_policy.GOVERNED_EGRESS_PROOF_LABEL] = self._egress_policy.governed_egress_proof_label(proof)
                except ValueError:
                    return None
                persisted_labels = lease_payload.get("labels")
                if isinstance(persisted_labels, dict):
                    runtime_subject = persisted_labels.get("ai-platform.runtime_subject")
                    if isinstance(runtime_subject, str) and runtime_subject:
                        labels["ai-platform.runtime_subject"] = runtime_subject
                settings = self._settings()
                if not self._egress_policy.is_governed_egress_proof(
                    proof,
                    provider="opensandbox",
                    signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
                    signing_key_id=getattr(
                        settings,
                        "sandbox_egress_proof_key_id",
                        self._egress_policy.GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
                    ),
                    previous_signing_keys=self._egress_policy.governed_egress_previous_signing_keys(
                        getattr(settings, "sandbox_egress_proof_previous_keys_json", "")
                    ),
                    allow_previous_keys=True,
                    expected_binding={
                        "attempt_id": attempt_id,
                        **(
                            {"network_name": lease_payload["governed_egress_network_name"]}
                            if "governed_egress_network_name" in lease_payload
                            else {}
                        ),
                    },
                    require_fresh=False,
                    allow_legacy_opensandbox=True,
                ):
                    return None
        lease = self._lease_factory(
            container_id=container_id,
            container_name=container_name,
            provider=provider,
            executor_url=executor_url,
            tenant_id=str(row["tenant_id"]),
            workspace_id=str(row["workspace_id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            run_id=run_id,
            sandbox_mode=str(row["sandbox_mode"]),
            browser_enabled=bool(row.get("browser_enabled")),
            workspace_host_path="",
            workspace_container_path=workspace_container_path,
            labels=labels,
        )
        if historical_internal_test:
            if self._opensandbox_policy.historical_internal_test_cleanup_expected_labels(lease) is None:
                return None
            if not allow_historical_internal_test_cleanup and not self._opensandbox_policy.active_internal_test_lease_is_authorized(lease, self._settings()):
                return None
        return lease

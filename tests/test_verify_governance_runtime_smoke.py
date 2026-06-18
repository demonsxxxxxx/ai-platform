import importlib
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def load_smoke_module():
    try:
        return importlib.import_module("tools.verify_governance_runtime_smoke")
    except ModuleNotFoundError as exc:
        raise AssertionError("tools.verify_governance_runtime_smoke is missing") from exc


def run_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def governance_payload(tenant_id="default", *, overrides=None):
    payload = {
        "tenant_id": tenant_id,
        "observability_readiness": {
            "domains": {
                "error_taxonomy": {
                    "evidence": {
                        "error_taxonomy_dashboard": {
                            "dashboard_contract": {
                                "forbidden_payload_classes": ["executor private payload"]
                            }
                        }
                    }
                },
                "alerts_and_exports": {
                    "next_checks": [
                        "keep executor private payload out of reviewed trace exports",
                    ]
                }
            }
        },
        "governance": {
            "schema_version": "ai-platform.governance-readiness.v1",
            "gate": "G6 Tool / Skill / Memory Governance",
            "status": "partial_blocked",
            "domains": {
                "tool_permission": {
                    "status": "partial_blocked",
                    "implemented": [
                        "tool_allow_deny_ask_policy_taxonomy_evidence",
                        "admin_policy_bulk_review_dashboard_contract",
                    ],
                    "gaps": ["legacy_frontend_route_policy_enforcement_or_ai_platform_remap"],
                    "evidence": {
                        "tool_policy_taxonomy": {
                            "schema_version": "ai-platform.tool-policy-readiness.v1",
                            "status": "partial_blocked",
                            "open_gaps": ["legacy_frontend_route_policy_enforcement_or_ai_platform_remap"],
                        },
                        "admin_policy_bulk_review_dashboard": {
                            "schema_version": "ai-platform.tool-policy-bulk-review-readiness.v1",
                            "status": "contract_only",
                            "open_gaps": ["runtime_acceptance"],
                            "does_not_close_g6": True,
                        },
                    },
                },
                "skill_governance": {
                    "status": "partial_blocked",
                    "implemented": [
                        "skill_version_registry",
                        "skill_snapshot_and_release_decision_lock",
                        "admin_skill_release_dashboard_contract",
                    ],
                    "gaps": ["skill_dependency_review_policy_runtime_acceptance"],
                    "evidence": {
                        "release_readiness": {
                            "schema_version": "ai-platform.skill-release-readiness.v1",
                            "status": "partial_blocked",
                            "summary": {
                                "total_skills": 2,
                                "public_workbench_skills": 1,
                                "internal_dependency_skills": 1,
                                "skills_with_declared_dependencies": 1,
                            },
                            "dependency_review_policy": {
                                "schema_version": "ai-platform.skill-dependency-review-policy.v1",
                                "status": "contract_only_not_runtime_satisfied",
                                "required_review_flags": [
                                    "sbom_reviewed",
                                    "license_policy_reviewed",
                                    "vulnerability_reviewed",
                                ],
                                "does_not_close_g6": True,
                            },
                            "dependency_review_runtime_acceptance_contract": {
                                "schema_version": (
                                    "ai-platform.skill-dependency-review-runtime-acceptance.v1"
                                ),
                                "verifier_script": "tools/verify_governance_runtime_smoke.py",
                                "verifier_schema_version": "ai-platform.governance-runtime-smoke.v1",
                                "runtime_payload_schema_version": (
                                    "ai-platform.skill-dependency-review-runtime-acceptance.v1"
                                ),
                                "target": "211_api_admin_runtime",
                                "acceptance_gap": "skill_dependency_review_policy_runtime_acceptance",
                                "runtime_acceptance_requires_real_admin_runtime_payload": True,
                                "required_verifier_checks": [
                                    "check_admin_runtime_governance_projection",
                                    "check_skill_dependency_review_runtime_acceptance",
                                    "check_no_secret_leakage",
                                ],
                                "required_runtime_checks": [
                                    "ordinary_user_admin_runtime_denied",
                                    "same_tenant_admin_runtime_projection",
                                    "skill_release_readiness_present",
                                    "dependency_review_policy_present",
                                    "review_manifest_flags_projected",
                                    "skill_inventory_summary_projected",
                                    "raw_skill_package_storage_absent",
                                    "executor_private_material_absent",
                                    "sandbox_working_directory_absent",
                                    "secret_like_values_absent",
                                ],
                                "non_expansion_invariants": {
                                    "ordinary_user_multi_agent_allowed": False,
                                    "long_term_cross_session_memory_enabled": False,
                                    "production_concurrency_defaults_raised": False,
                                    "docker_sandbox_production_hardening_claimed": False,
                                },
                                "does_not_close_g6": True,
                            },
                            "closed_runtime_gaps": [],
                            "runtime_acceptance_evidence": {},
                            "open_gaps": ["skill_dependency_review_policy_runtime_acceptance"],
                        },
                        "admin_skill_release_dashboard": {
                            "schema_version": "ai-platform.skill-release-dashboard-readiness.v1",
                            "status": "contract_only",
                            "open_gaps": ["admin_skill_release_dashboard_211_acceptance"],
                            "does_not_close_g6": True,
                        },
                    },
                },
                "memory_governance": {
                    "status": "partial_blocked",
                    "implemented": [
                        "long_term_cross_session_memory_default_fail_closed",
                        "context_snapshot_public_provenance_projection_contract",
                        "executor_context_pack_prompt_injection_source_tests",
                    ],
                    "gaps": [
                        "office_context_pack_persistence_and_versioning",
                        "executor_context_pack_211_acceptance",
                        "frontend_context_provenance_acceptance",
                    ],
                    "evidence": {
                        "office_context_pack_readiness": {
                            "schema_version": "ai-platform.office-context-pack-readiness.v1",
                            "status": "partial_blocked",
                            "implemented_controls": [
                                "source_level_context_pack_contract",
                                "context_snapshot_public_provenance_projection_contract",
                                "executor_context_pack_prompt_injection_source_tests",
                            ],
                            "summary": {
                                "open_gaps": 3,
                                "sandbox_default_for_lightweight_office_tasks": False,
                            },
                        }
                    },
                },
            },
            "open_gaps": [
                "legacy_frontend_route_policy_enforcement_or_ai_platform_remap",
                "skill_dependency_review_policy_runtime_acceptance",
                "office_context_pack_persistence_and_versioning",
                "executor_context_pack_211_acceptance",
            ],
            "evidence_policy": "code_tests_docs_and_211_smoke_required_before_gate_closure",
        },
    }
    if overrides:
        payload.update(overrides)
    return payload


class GovernanceRuntimeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview"):
            if self.headers.get("X-AI-Gateway-Secret", "") != "test-secret":
                self._send_json(403, {"detail": "invalid_gateway_principal_secret"})
                return
            roles = self.headers.get("X-AI-Roles", "")
            if "admin" not in roles:
                self._send_json(403, {"detail": "not_ai_admin"})
                return
            self._send_json(200, governance_payload(self.headers.get("X-AI-Tenant-ID", "default")))
            return
        self._send_json(404, {"detail": "not_found"})

    def _send_json(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MissingMemoryDomainHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            del payload["governance"]["domains"]["memory_governance"]
            self._send_json(200, payload)
            return
        super().do_GET()


class MissingImplementedControlHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["governance"]["domains"]["tool_permission"]["implemented"] = []
            payload["governance"]["domains"]["skill_governance"]["implemented"] = []
            self._send_json(200, payload)
            return
        super().do_GET()


class MissingDependencyReviewPolicyHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            release_readiness = payload["governance"]["domains"]["skill_governance"]["evidence"][
                "release_readiness"
            ]
            release_readiness.pop("dependency_review_policy")
            release_readiness["summary"] = {}
            self._send_json(200, payload)
            return
        super().do_GET()


class LeakyGovernanceHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["governance"]["domains"]["memory_governance"]["evidence"][
                "executor_private_payload"
            ] = {"raw_storage_key": "tenant/default/runs/run-a/private.json"}
            self._send_json(200, payload)
            return
        super().do_GET()


class LeakyPolicyTextHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["observability_readiness"]["domains"]["alerts_and_exports"]["next_checks"].append(
                "do not expose executor_private_payload={\"raw_storage_key\":\"tenant/default/runs/run-a/private.json\"}"
            )
            self._send_json(200, payload)
            return
        super().do_GET()


class RequiredEvidencePolicyTextHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["governance"]["domains"]["memory_governance"]["evidence"][
                "b1_memory_context_readiness"
            ] = {
                "schema_version": "ai-platform.b1-memory-context-readiness.v1",
                "runtime_acceptance": {
                    "required_evidence": [
                        "redaction scan proves executor private payload and callback token material are absent",
                    ]
                },
            }
            self._send_json(200, payload)
            return
        super().do_GET()


class LeakyRequiredEvidencePolicyTextHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["governance"]["domains"]["memory_governance"]["evidence"][
                "b1_memory_context_readiness"
            ] = {
                "schema_version": "ai-platform.b1-memory-context-readiness.v1",
                "runtime_acceptance": {
                    "required_evidence": [
                        "executor-private payload: tenant/default/runs/run-a/private.json",
                    ]
                },
            }
            self._send_json(200, payload)
            return
        super().do_GET()


class SkillDashboardContractLeakHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["governance"]["domains"]["skill_governance"]["evidence"][
                "admin_skill_release_dashboard"
            ]["dashboard_contract"] = {"columns": ["skill_id", "status"]}
            self._send_json(200, payload)
            return
        super().do_GET()


class NestedSkillDashboardContractLeakHandler(GovernanceRuntimeHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/ai/admin/runtime/overview") and "admin" in self.headers.get("X-AI-Roles", ""):
            payload = governance_payload(self.headers.get("X-AI-Tenant-ID", "default"))
            payload["governance"]["domains"]["skill_governance"]["evidence"][
                "release_readiness"
            ]["dashboard_contract"] = {"columns": ["skill_id", "status"]}
            self._send_json(200, payload)
            return
        super().do_GET()


def test_governance_runtime_smoke_checks_admin_only_governance_domains_and_redaction():
    smoke = load_smoke_module()
    server = run_server(GovernanceRuntimeHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            commit_sha="820669037978237182ecd2fd27c2ffa10a953c0b",
            runtime_subject_commit_sha="2384e19dcac2e39fbcf9c27dc990f5774d391422",
            image="ai-platform:2384e19-context-source-provenance",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is True
    assert payload["schema_version"] == "ai-platform.governance-runtime-smoke.v1"
    assert payload["redaction_scan_status"] == "passed"
    assert payload["source"]["gateway_secret_supplied"] is True
    assert payload["source"]["runtime_subject_commit_sha"] == "2384e19dcac2e39fbcf9c27dc990f5774d391422"
    assert payload["checks"]["ordinary_admin_runtime"]["status"] == 403
    admin = payload["checks"]["admin_runtime_governance"]
    assert admin["status"] == 200
    assert admin["tenant_matches_requested"] is True
    assert admin["governance_schema_version"] == "ai-platform.governance-readiness.v1"
    assert admin["governance_status"] == "partial_blocked"
    assert admin["required_domains_present"] is True
    assert admin["tool_permission"]["taxonomy_present"] is True
    assert admin["tool_permission"]["bulk_review_present"] is True
    assert admin["tool_permission"]["implemented_policy_taxonomy"] is True
    assert admin["tool_permission"]["implemented_bulk_review_dashboard"] is True
    assert admin["skill_governance"]["release_readiness_present"] is True
    assert admin["skill_governance"]["dependency_review_policy_present"] is True
    assert admin["skill_governance"]["runtime_acceptance_contract_present"] is True
    assert admin["skill_governance"]["review_manifest_flags_projected"] is True
    assert admin["skill_governance"]["skill_inventory_summary_projected"] is True
    assert admin["skill_governance"]["runtime_gap_open"] is True
    assert admin["skill_governance"]["dashboard_present"] is True
    assert admin["skill_governance"]["dashboard_contract_exposed"] is False
    assert admin["skill_governance"]["implemented_version_registry"] is True
    assert admin["skill_governance"]["implemented_snapshot_lock"] is True
    assert admin["memory_governance"]["long_term_fail_closed_present"] is True
    assert admin["memory_governance"]["context_provenance_present"] is True
    assert admin["memory_governance"]["office_context_readiness_present"] is True
    acceptance = payload["checks"]["skill_dependency_review_policy_runtime_acceptance"]
    assert acceptance == {
        "schema_version": "ai-platform.skill-dependency-review-runtime-acceptance.v1",
        "status": "verified_runtime_acceptance",
        "target": "211_api_admin_runtime",
        "runtime_acceptance_requires_real_admin_runtime_payload": False,
        "does_not_close_runtime_acceptance": False,
        "runtime_payload_verified": True,
        "checks": {
            "ordinary_user_admin_runtime_denied": True,
            "same_tenant_admin_runtime_projection": True,
            "skill_release_readiness_present": True,
            "dependency_review_policy_present": True,
            "review_manifest_flags_projected": True,
            "skill_inventory_summary_projected": True,
            "raw_skill_package_storage_absent": True,
            "executor_private_material_absent": True,
            "sandbox_working_directory_absent": True,
            "secret_like_values_absent": True,
        },
        "non_expansion_invariants": {
            "ordinary_user_multi_agent_allowed": False,
            "long_term_cross_session_memory_enabled": False,
            "production_concurrency_defaults_raised": False,
            "docker_sandbox_production_hardening_claimed": False,
        },
    }
    verifier_checks = payload["checks"]["verifier_checks"]
    assert {"name": "check_admin_runtime_governance_projection", "passed": True} in verifier_checks
    assert {"name": "check_skill_dependency_review_runtime_acceptance", "passed": True} in verifier_checks
    assert {"name": "check_no_secret_leakage", "passed": True} in verifier_checks
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "test-secret" not in serialized
    assert "executor_private_payload" not in serialized


def test_governance_runtime_smoke_fails_closed_when_required_domain_is_missing():
    smoke = load_smoke_module()
    server = run_server(MissingMemoryDomainHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    admin = payload["checks"]["admin_runtime_governance"]
    assert admin["required_domains_present"] is False
    assert admin["missing_domains"] == ["memory_governance"]


def test_governance_runtime_smoke_fails_closed_when_implemented_controls_missing():
    smoke = load_smoke_module()
    server = run_server(MissingImplementedControlHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    admin = payload["checks"]["admin_runtime_governance"]
    assert admin["tool_permission"]["implemented_policy_taxonomy"] is False
    assert admin["tool_permission"]["implemented_bulk_review_dashboard"] is False
    assert admin["skill_governance"]["implemented_version_registry"] is False
    assert admin["skill_governance"]["implemented_snapshot_lock"] is False
    acceptance = payload["checks"]["skill_dependency_review_policy_runtime_acceptance"]
    assert acceptance["runtime_payload_verified"] is True
    assert {"name": "check_admin_runtime_governance_projection", "passed": False} in payload[
        "checks"
    ]["verifier_checks"]
    assert {"name": "check_skill_dependency_review_runtime_acceptance", "passed": True} in payload[
        "checks"
    ]["verifier_checks"]


def test_governance_runtime_smoke_fails_closed_when_dependency_review_policy_missing():
    smoke = load_smoke_module()
    server = run_server(MissingDependencyReviewPolicyHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    acceptance = payload["checks"]["skill_dependency_review_policy_runtime_acceptance"]
    assert acceptance["status"] == "blocked_runtime_acceptance"
    assert acceptance["runtime_payload_verified"] is False
    assert acceptance["checks"]["dependency_review_policy_present"] is False
    assert acceptance["checks"]["review_manifest_flags_projected"] is False
    assert acceptance["checks"]["skill_inventory_summary_projected"] is False
    assert {"name": "check_skill_dependency_review_runtime_acceptance", "passed": False} in payload[
        "checks"
    ]["verifier_checks"]


def test_governance_runtime_smoke_fails_closed_on_private_projection_marker():
    smoke = load_smoke_module()
    server = run_server(LeakyGovernanceHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime_governance"]["forbidden_projection_terms_present"] is True
    assert payload["redaction_scan_status"] == "failed"


def test_governance_runtime_smoke_fails_closed_on_private_value_inside_policy_text():
    smoke = load_smoke_module()
    server = run_server(LeakyPolicyTextHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime_governance"]["forbidden_projection_terms_present"] is True
    assert payload["redaction_scan_status"] == "failed"


def test_governance_runtime_smoke_allows_required_evidence_policy_labels():
    smoke = load_smoke_module()
    server = run_server(RequiredEvidencePolicyTextHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is True
    assert payload["checks"]["admin_runtime_governance"]["forbidden_projection_terms_present"] is False
    assert payload["redaction_scan_status"] == "passed"


def test_governance_runtime_smoke_fails_closed_on_private_value_inside_required_evidence():
    smoke = load_smoke_module()
    server = run_server(LeakyRequiredEvidencePolicyTextHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime_governance"]["forbidden_projection_terms_present"] is True
    assert payload["redaction_scan_status"] == "failed"


def test_governance_runtime_smoke_fails_closed_if_skill_dashboard_contract_leaks():
    smoke = load_smoke_module()
    server = run_server(SkillDashboardContractLeakHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime_governance"]["skill_governance"][
        "dashboard_contract_exposed"
    ] is True


def test_governance_runtime_smoke_fails_closed_if_nested_skill_dashboard_contract_leaks():
    smoke = load_smoke_module()
    server = run_server(NestedSkillDashboardContractLeakHandler)
    try:
        payload = smoke.build_governance_runtime_smoke(
            base_url=f"http://127.0.0.1:{server.server_port}",
            gateway_secret="test-secret",
            timeout_seconds=5,
        )
    finally:
        server.shutdown()

    assert payload["ok"] is False
    assert payload["checks"]["admin_runtime_governance"]["skill_governance"][
        "dashboard_contract_exposed"
    ] is True


def test_governance_runtime_smoke_cli_emits_safe_json_and_exit_status():
    server = run_server(GovernanceRuntimeHandler)
    env = {
        **os.environ,
        "TEST_AI_PLATFORM_GATEWAY_SECRET": "test-secret",
    }
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path("tools") / "verify_governance_runtime_smoke.py"),
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--gateway-secret-env",
                "TEST_AI_PLATFORM_GATEWAY_SECRET",
                "--commit-sha",
                "820669037978237182ecd2fd27c2ffa10a953c0b",
                "--runtime-subject-commit-sha",
                "2384e19dcac2e39fbcf9c27dc990f5774d391422",
                "--image",
                "ai-platform:2384e19-context-source-provenance",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "ai-platform.governance-runtime-smoke.v1"
    assert "test-secret" not in result.stdout

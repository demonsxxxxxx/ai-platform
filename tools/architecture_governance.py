"""Evaluate an exact Git range against the trusted backend architecture policy.

The executable, policy schema, and normal policy are authority objects. Candidate
filesystem contents cannot relax their own result. A narrowly bounded recovery
path may replace only an invalid app-root inventory with the exact Git inventory.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import keyword
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any


REPORT_SCHEMA_VERSION = "ai-platform.architecture-governance-report.v1"
POLICY_SCHEMA_VERSION = "ai-platform.architecture-policy.v1"
POLICY_SCHEMA_ID = (
    "https://github.com/demonsxxxxxx/ai-platform/"
    "schemas/architecture-policy.v1.schema.json"
)
POLICY_PATH = "architecture-policy.json"
POLICY_SCHEMA_PATH = "schemas/architecture-policy.v1.schema.json"
TOOL_PATH = "tools/architecture_governance.py"
EXCEPTION_PATH = ".architecture-governance-exception.json"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
MODULE_NAME = re.compile(r"[a-z][a-z0-9_]*")
SYMBOL_NAME = re.compile(r"[A-Z][A-Z0-9_]+")
SQL_TEXT = re.compile(r"^\s*(?:select|insert|update|delete)\b", re.IGNORECASE)
SUPPORTED_SCHEMA_PATTERNS = frozenset(
    {
        r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.\.(?:/|$)).+$",
        r"^[A-Z][A-Z0-9_]+$",
        r"^[A-Z][A-Za-z0-9]*$",
        r"^_?[A-Za-z][A-Za-z0-9_]*$",
        r"^[a-z][a-z0-9_]*$",
        r"^_?[a-z][a-z0-9_]*$",
        r"^app(?:\.[a-z][a-z0-9_]*)+$",
    }
)
ALLOWED_PLATFORM_MIGRATION_TARGETS = frozenset(
    {
        "app.platform.postgres.errors",
        "app.platform.postgres.limits",
    }
)

POLICY_KEYS = {
    "schema_version",
    "owner",
    "reason",
    "source_contract",
    "app_root",
    "target_packages",
    "bounded_contexts",
    "layers",
    "public_cross_domain_modules",
    "public_kernel_modules",
    "approved_root_modules",
    "forbidden_module_names",
    "forbidden_delivery_tokens",
    "frozen_hot_files",
    "compatibility_facades",
    "migration_bridges",
    "legacy_api_cutovers",
    "production_registries",
    "governed_symbols",
    "exception_contract",
}
LAYER_NAMES = ("application", "domain", "infrastructure", "transport")
BUILTIN_NON_EXEMPTIBLE_CODES = frozenset({
    "compatibility_import_forbidden",
    "cross_domain_internal_import",
    "facade_missing",
    "facade_wildcard_import",
    "governed_symbol_missing",
    "governed_symbol_owner",
    "kernel_product_import",
    "kernel_public_surface_forbidden",
    "layer_external_dependency_forbidden",
    "layer_dependency_forbidden",
    "migration_bridge_import_contract",
    "migration_bridge_source_growth",
    "migration_bridge_source_logic",
    "migration_bridge_symbol_contract",
    "migration_bridge_target_contract",
    "legacy_api_cutover_contract",
    "legacy_api_cutover_retirement_required",
    "legacy_api_cutover_source_logic",
    "legacy_api_cutover_target_contract",
    "platform_product_import",
    "registry_adapter_mismatch",
    "registry_duplicate_key",
    "registry_dynamic_selector",
    "registry_factory_contract",
    "registry_missing_key",
    "registry_missing",
    "registry_nonliteral_key",
    "registry_selector_mismatch",
    "registry_test_double",
    "registry_unknown_key",
})


class ArchitectureError(RuntimeError):
    """Describe a trusted-input, Git, policy, or exception failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _ArchitectureArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArchitectureError("invalid_cli", message)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


class _CommandRunner:
    def run(self, command: Sequence[str], *, cwd: Path) -> _CommandResult:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        return _CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def run_bytes(self, command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(list(command), cwd=cwd, check=False, capture_output=True)


@dataclass(frozen=True)
class Finding:
    """One deterministic architecture-policy finding."""

    code: str
    message: str
    path: str
    line: int = 0
    exemptible: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "details": self.details,
            "exemptible": self.exemptible,
            "line": self.line,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True)
class Evaluation:
    authority_ref: str
    base_ref: str
    head_ref: str
    status: str
    policy: dict[str, Any]
    findings: tuple[Finding, ...]
    exempted_findings: tuple[Finding, ...]
    exception: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return 0 if self.status == "pass" else 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "authority_ref": self.authority_ref,
            "base_ref": self.base_ref,
            "exception": self.exception,
            "exempted_findings": [item.as_dict() for item in self.exempted_findings],
            "findings": [item.as_dict() for item in self.findings],
            "head_ref": self.head_ref,
            "policy": self.policy,
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": self.status,
        }


@dataclass(frozen=True)
class _ChangedPath:
    status: str
    old_path: str | None
    new_path: str | None

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class _ImportEdge:
    target: str
    line: int


@dataclass(frozen=True)
class _ModuleLocation:
    package: str | None
    context: str | None
    layer: str | None
    boundary: str | None


class _GitObjects:
    def __init__(self, repo_root: Path, runner: _CommandRunner) -> None:
        self.repo_root = repo_root
        self.runner = runner

    def resolve_commit(self, value: str, label: str) -> str:
        if FULL_SHA.fullmatch(value) is None:
            raise ArchitectureError("invalid_ref", f"{label} must be a lowercase full 40-hex commit id")
        result = self.runner.run(
            ("git", "rev-parse", "--verify", f"{value}^{{commit}}"),
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            raise ArchitectureError("missing_ref", f"{label} commit object is unavailable")
        if result.stdout.strip() != value:
            raise ArchitectureError("invalid_ref", f"{label} did not resolve to the exact supplied commit id")
        return value

    def require_ancestor(self, older: str, newer: str, *, code: str, message: str) -> None:
        result = self.runner.run(
            ("git", "merge-base", "--is-ancestor", older, newer),
            cwd=self.repo_root,
        )
        if result.returncode == 1:
            raise ArchitectureError(code, message)
        if result.returncode != 0:
            raise ArchitectureError("git_failed", _command_failure("git merge-base", result))

    def blob(self, commit: str, path: str, *, required: bool = True) -> bytes | None:
        result = self.runner.run_bytes(("git", "show", f"{commit}:{path}"), cwd=self.repo_root)
        if result.returncode == 0:
            return result.stdout
        if not required:
            return None
        raise ArchitectureError("git_object_missing", f"required Git object is unavailable: {commit}:{path}")

    def text(self, commit: str, path: str, *, required: bool = True) -> str | None:
        content = self.blob(commit, path, required=required)
        if content is None:
            return None
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchitectureError("source_not_utf8", f"Git object must be UTF-8 text: {path}") from exc

    def paths(self, commit: str, prefix: str) -> tuple[str, ...]:
        result = self.runner.run_bytes(
            ("git", "ls-tree", "-r", "--name-only", "-z", commit, "--", prefix),
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            raise ArchitectureError("git_failed", "git ls-tree failed")
        return tuple(sorted(part.decode("utf-8") for part in result.stdout.split(b"\0") if part))

    def changes(self, base: str, head: str) -> tuple[_ChangedPath, ...]:
        result = self.runner.run_bytes(
            (
                "git",
                "-c",
                "core.quotepath=false",
                "diff",
                "--name-status",
                "-z",
                "--find-renames=50%",
                base,
                head,
                "--",
            ),
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            raise ArchitectureError("git_failed", "git diff --name-status failed")
        return _parse_changes(result.stdout)

    def exception_scope_sha256(self, base: str, head: str, exception_path: str) -> str:
        result = self.runner.run_bytes(
            (
                "git",
                "-c",
                "core.quotepath=false",
                "diff",
                "--raw",
                "--no-abbrev",
                "-z",
                "--no-renames",
                base,
                head,
                "--",
                ".",
                f":(exclude){exception_path}",
            ),
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            raise ArchitectureError("git_failed", "git diff --raw failed")
        return hashlib.sha256(result.stdout).hexdigest()


class ArchitectureEvaluator:
    """Evaluate one candidate with the immutable authority policy."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: _CommandRunner | None = None,
        today: date | None = None,
        tool_path: Path | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._runner = runner or _CommandRunner()
        self._today = today or datetime.now(UTC).date()
        self._tool_path = (tool_path or Path(__file__)).resolve()
        self._git = _GitObjects(self._repo_root, self._runner)

    def evaluate(self, authority_ref: str, base_ref: str, head_ref: str) -> Evaluation:
        self._discover_repository()
        authority = self._git.resolve_commit(authority_ref, "authority_ref")
        base = self._git.resolve_commit(base_ref, "base_ref")
        head = self._git.resolve_commit(head_ref, "head_ref")
        self._git.require_ancestor(
            authority,
            base,
            code="authority_not_ancestor",
            message="authority_ref must be an ancestor of base_ref",
        )
        self._git.require_ancestor(
            base,
            head,
            code="non_ancestor_range",
            message="base_ref must be an ancestor of head_ref",
        )
        self._assert_authority_source(authority)
        schema = _load_json_object(
            self._git.text(authority, POLICY_SCHEMA_PATH),
            path=POLICY_SCHEMA_PATH,
            error_code="invalid_policy_schema",
        )
        _validate_policy_schema(schema)
        policy = _load_json_object(
            self._git.text(authority, POLICY_PATH),
            path=POLICY_PATH,
            error_code="invalid_policy",
        )
        _validate_json_schema_instance(policy, schema)
        changes = self._git.changes(base, head)
        if authority != base and any(change.path == POLICY_PATH for change in changes):
            self._reject_invalid_base_policy_repair(
                schema=schema,
                base=base,
            )
        try:
            _validate_policy(policy, self._git, authority)
        except ArchitectureError as exc:
            if (
                exc.code != "invalid_policy"
                or str(exc) != "approved_root_modules must exactly inventory authority app-root Python modules"
                or authority == head
            ):
                raise
            policy = self._load_root_inventory_repair(
                authority_policy=policy,
                schema=schema,
                authority=authority,
                base=base,
                head=head,
                changes=changes,
            )

        findings = self._evaluate_candidate(policy, base, head, changes)
        ordered = _sort_findings(findings)
        active, exempted, exception = self._apply_exception(
            policy,
            authority,
            base,
            head,
            ordered,
        )
        return Evaluation(
            authority_ref=authority,
            base_ref=base,
            head_ref=head,
            status="pass" if not active else "violation",
            policy={
                "owner": policy["owner"],
                "path": POLICY_PATH,
                "schema_path": POLICY_SCHEMA_PATH,
                "schema_version": policy["schema_version"],
                "source_contract": policy["source_contract"],
            },
            findings=tuple(active),
            exempted_findings=tuple(exempted),
            exception=exception,
        )

    def _reject_invalid_base_policy_repair(
        self,
        *,
        schema: dict[str, Any],
        base: str,
    ) -> None:
        base_policy = _load_json_object(
            self._git.text(base, POLICY_PATH),
            path=POLICY_PATH,
            error_code="invalid_policy_repair",
        )
        try:
            _validate_json_schema_instance(base_policy, schema)
            _validate_policy(base_policy, self._git, base)
        except ArchitectureError as exc:
            raise ArchitectureError(
                "invalid_policy_repair",
                "policy changes cannot repair an invalid base when authority_ref differs from base_ref",
            ) from exc

    def _load_root_inventory_repair(
        self,
        *,
        authority_policy: dict[str, Any],
        schema: dict[str, Any],
        authority: str,
        base: str,
        head: str,
        changes: Sequence[_ChangedPath],
    ) -> dict[str, Any]:
        if authority != base:
            raise ArchitectureError(
                "invalid_policy_repair",
                "root inventory repair requires authority_ref to equal base_ref",
            )

        policy_changes = [change for change in changes if change.path == POLICY_PATH]
        exception_changes = [change for change in changes if change.path == EXCEPTION_PATH]
        changed_paths = {
            path
            for change in changes
            for path in (change.old_path, change.new_path)
            if path is not None
        }
        if changed_paths - {POLICY_PATH, EXCEPTION_PATH}:
            raise ArchitectureError(
                "invalid_policy_repair",
                "root inventory repair cannot change files outside the policy and stale exception",
            )
        if len(policy_changes) != 1 or policy_changes[0] != _ChangedPath("M", POLICY_PATH, POLICY_PATH):
            raise ArchitectureError(
                "invalid_policy_repair",
                "root inventory repair must modify architecture-policy.json in place",
            )
        if exception_changes and exception_changes != [_ChangedPath("D", EXCEPTION_PATH, None)]:
            raise ArchitectureError(
                "invalid_policy_repair",
                "root inventory repair may only delete the stale architecture exception",
            )
        if self._git.blob(head, EXCEPTION_PATH, required=False) is not None:
            raise ArchitectureError(
                "invalid_policy_repair",
                "root inventory repair requires the candidate architecture exception to be absent",
            )

        candidate_policy = _load_json_object(
            self._git.text(head, POLICY_PATH),
            path=POLICY_PATH,
            error_code="invalid_policy_repair",
        )
        _validate_json_schema_instance(candidate_policy, schema)
        authority_contract = copy.deepcopy(authority_policy)
        candidate_contract = copy.deepcopy(candidate_policy)
        authority_contract.pop("approved_root_modules", None)
        candidate_contract.pop("approved_root_modules", None)
        if candidate_contract != authority_contract:
            raise ArchitectureError(
                "invalid_policy_repair",
                "root inventory repair cannot change any other architecture policy field",
            )

        authority_roots = sorted(
            path
            for path in self._git.paths(authority, "app")
            if len(PurePosixPath(path).parts) == 2 and PurePosixPath(path).suffix == ".py"
        )
        head_roots = sorted(
            path
            for path in self._git.paths(head, "app")
            if len(PurePosixPath(path).parts) == 2 and PurePosixPath(path).suffix == ".py"
        )
        if authority_roots != head_roots or candidate_policy["approved_root_modules"] != authority_roots:
            raise ArchitectureError(
                "invalid_policy_repair",
                "approved_root_modules must exactly match the unchanged authority and candidate Git trees",
            )

        _validate_policy(candidate_policy, self._git, head)
        return candidate_policy

    def _discover_repository(self) -> None:
        result = self._runner.run(("git", "rev-parse", "--show-toplevel"), cwd=self._repo_root)
        if result.returncode != 0:
            raise ArchitectureError("not_git_repository", "current directory is not inside a Git repository")
        discovered = Path(result.stdout.strip()).resolve()
        if discovered != self._repo_root:
            self._repo_root = discovered
            self._git = _GitObjects(discovered, self._runner)

    def _assert_authority_source(self, authority: str) -> None:
        expected = self._git.blob(authority, TOOL_PATH)
        try:
            actual = self._tool_path.read_bytes()
        except OSError as exc:
            raise ArchitectureError("authority_source_unavailable", str(exc)) from exc
        if actual != expected:
            raise ArchitectureError(
                "authority_source_mismatch",
                "the running checker must be the exact tools/architecture_governance.py authority object",
            )

    def _evaluate_candidate(
        self,
        policy: dict[str, Any],
        base: str,
        head: str,
        changes: Sequence[_ChangedPath],
    ) -> list[Finding]:
        findings: list[Finding] = []
        changed_by_head = {item.new_path: item for item in changes if item.new_path is not None}
        target_packages = set(policy["target_packages"])
        approved_roots = set(policy["approved_root_modules"])
        forbidden_names = set(policy["forbidden_module_names"])
        forbidden_tokens = set(policy["forbidden_delivery_tokens"])
        base_modules = _python_modules(self._git.paths(base, "app"))
        head_modules = _python_modules(self._git.paths(head, "app"))

        for change in changes:
            path = change.new_path
            if path is None or not _is_python_path(path) or not path.startswith("app/"):
                continue
            old_path = change.old_path
            is_new_location = old_path is None or old_path != path
            parts = PurePosixPath(path).parts
            if is_new_location and len(parts) == 2 and path not in approved_roots:
                findings.append(
                    Finding(
                        "unapproved_app_root_module",
                        "new app-root modules require an explicit architecture-policy owner",
                        path,
                    )
                )
            if is_new_location and len(parts) >= 3 and parts[1] not in target_packages:
                findings.append(
                    Finding(
                        "unapproved_app_package",
                        "new modules cannot extend a legacy or undeclared app package",
                        path,
                    )
                )
            boundary_files = {
                f"{name}.py" for name in policy["public_cross_domain_modules"]
            } | {"registry.py", "__init__.py"}
            if (
                is_new_location
                and len(parts) >= 3
                and parts[1] in set(policy["bounded_contexts"])
                and parts[2] not in set(policy["layers"]) | boundary_files
            ):
                findings.append(
                    Finding(
                        "unlayered_domain_module",
                        "new domain modules must live in a declared layer or api.py/events.py/registry.py boundary",
                        path,
                    )
                )
            stem = PurePosixPath(path).stem.lower()
            if is_new_location and stem in forbidden_names:
                findings.append(
                    Finding(
                        "generic_module_name",
                        f"generic module name '{stem}' does not identify one owned concept",
                        path,
                    )
                )
            name_tokens = set(stem.split("_"))
            matched_delivery = sorted(name_tokens & forbidden_tokens)
            if is_new_location and matched_delivery:
                findings.append(
                    Finding(
                        "delivery_label_module",
                        "delivery/evidence labels cannot name new production modules",
                        path,
                        details={"tokens": matched_delivery},
                    )
                )

            head_text = self._git.text(head, path)
            head_tree = _parse_python(head_text, path, candidate=True)
            bridge_targets = _active_migration_bridge_targets(policy, path, head_tree)
            base_targets: set[str] = set()
            base_text: str | None = None
            if old_path is not None:
                base_text = self._git.text(base, old_path, required=False)
                if base_text is not None:
                    base_tree = _parse_python(base_text, old_path, candidate=False)
                    if old_path == path:
                        base_targets = {
                            edge.target
                            for edge in _import_edges(
                                base_tree,
                                old_path,
                                known_modules=base_modules,
                            )
                        }
            for edge in _import_edges(head_tree, path, known_modules=head_modules):
                if edge.target in base_targets:
                    continue
                if edge.target in bridge_targets:
                    continue
                finding = _new_edge_finding(policy, path, edge)
                if finding is not None:
                    findings.append(finding)

            findings.extend(_governed_symbol_findings(policy, path, old_path, head_tree, self._git, base))

        changes_by_source = {
            item.old_path or item.new_path: item
            for item in changes
            if (item.old_path or item.new_path) is not None
        }
        for source_path in sorted(
            {bridge["source_path"] for bridge in policy["migration_bridges"]}
        ):
            change = changes_by_source.get(source_path)
            old_path = change.old_path if change is not None else source_path
            base_source = self._git.text(base, old_path or source_path, required=False)
            head_source = self._git.text(head, source_path, required=False)
            if head_source is None:
                findings.append(
                    Finding(
                        "migration_bridge_import_contract",
                        "a declared migration bridge source cannot be deleted or renamed before its authority entry is retired",
                        source_path,
                        exemptible=False,
                    )
                )
                continue
            head_tree = _parse_python(head_source, source_path, candidate=True)
            findings.extend(
                _migration_bridge_findings(
                    policy,
                    path=source_path,
                    old_path=old_path,
                    base_source=base_source,
                    head_source=head_source,
                    head_tree=head_tree,
                    git=self._git,
                    head=head,
                )
            )

        for cutover in policy["legacy_api_cutovers"]:
            source_path = cutover["source_path"]
            change = changes_by_source.get(source_path)
            old_path = change.old_path if change is not None else source_path
            base_source = self._git.text(base, old_path or source_path, required=False)
            head_source = self._git.text(head, source_path, required=False)
            findings.extend(
                _legacy_api_cutover_findings(
                    cutover,
                    path=source_path,
                    old_path=old_path,
                    base_source=base_source,
                    head_source=head_source,
                    git=self._git,
                    base=base,
                    head=head,
                )
            )

        for hot_file in policy["frozen_hot_files"]:
            path = hot_file["path"]
            if path not in changed_by_head:
                continue
            old_text = self._git.text(base, changed_by_head[path].old_path or path, required=False)
            head_text = self._git.text(head, path)
            old_lines = 0 if old_text is None else len(old_text.splitlines())
            head_lines = len(head_text.splitlines())
            if head_lines > old_lines or head_lines > hot_file["max_lines"]:
                findings.append(
                    Finding(
                        "frozen_hot_file_growth",
                        "frozen legacy modules must not grow while responsibilities are extracted",
                        path,
                        details={
                            "base_lines": old_lines,
                            "head_lines": head_lines,
                            "policy_max_lines": hot_file["max_lines"],
                        },
                    )
                )

        for facade in policy["compatibility_facades"]:
            path = facade["path"]
            facade_text = self._git.text(head, path, required=False)
            if facade_text is None:
                findings.append(
                    Finding(
                        "facade_missing",
                        "declared compatibility facade is missing from the candidate",
                        path,
                        exemptible=False,
                    )
                )
            else:
                findings.extend(_facade_findings(facade, facade_text, path))

        for registry in policy["production_registries"]:
            path = registry["path"]
            registry_text = self._git.text(head, path, required=False)
            if registry_text is None:
                findings.append(
                    Finding(
                        "registry_missing",
                        "declared production registry is missing from the candidate",
                        path,
                        exemptible=False,
                    )
                )
            else:
                findings.extend(_registry_findings(registry, registry_text, path))
            findings.extend(_registry_selector_findings(registry, self._git, head))

        for symbol in policy["governed_symbols"]:
            owner_text = self._git.text(head, symbol["path"], required=False)
            if owner_text is None:
                findings.append(
                    Finding(
                        "governed_symbol_missing",
                        f"canonical governed symbol {symbol['name']} owner file is missing",
                        symbol["path"],
                        exemptible=False,
                        details={"symbol": symbol["name"]},
                    )
                )
                continue
            owner_tree = _parse_python(owner_text, symbol["path"], candidate=True)
            if symbol["name"] not in _assignment_names(owner_tree):
                findings.append(
                    Finding(
                        "governed_symbol_missing",
                        f"canonical governed symbol {symbol['name']} is missing from its owner",
                        symbol["path"],
                        exemptible=False,
                        details={"symbol": symbol["name"]},
                    )
                )

        return _deduplicate_findings(findings)

    def _apply_exception(
        self,
        policy: dict[str, Any],
        authority: str,
        base: str,
        head: str,
        findings: Sequence[Finding],
    ) -> tuple[list[Finding], list[Finding], dict[str, Any]]:
        contract = policy["exception_contract"]
        exception_path = contract["path"]
        raw = self._git.text(head, exception_path, required=False)
        if raw is None:
            return list(findings), [], {"path": exception_path, "status": "absent"}
        payload = _load_json_object(raw, path=exception_path, error_code="invalid_exception")
        _validate_exception(
            payload,
            contract=contract,
            today=self._today,
            authority_ref=authority,
            base_ref=base,
            scope_sha256=self._git.exception_scope_sha256(base, head, exception_path),
        )
        candidate = payload["candidate"]

        requested = {(entry["code"], entry["path"]) for entry in payload["violations"]}
        non_exemptible = BUILTIN_NON_EXEMPTIBLE_CODES | set(contract["non_exemptible_codes"])
        blocked = sorted(code for code, _path in requested if code in non_exemptible)
        if blocked:
            raise ArchitectureError(
                "invalid_exception",
                f"non-exemptible architecture finding codes requested: {', '.join(blocked)}",
            )
        finding_counts: dict[tuple[str, str], int] = {}
        for item in findings:
            key = (item.code, item.path)
            finding_counts[key] = finding_counts.get(key, 0) + 1
        ambiguous = sorted(key for key in requested if finding_counts.get(key, 0) != 1)
        if ambiguous:
            rendered = ", ".join(f"{code}:{path}" for code, path in ambiguous)
            raise ArchitectureError(
                "invalid_exception",
                f"exception entries must identify exactly one finding: {rendered}",
            )
        matched = [item for item in findings if (item.code, item.path) in requested and item.exemptible]
        active = [item for item in findings if (item.code, item.path) not in requested or not item.exemptible]
        matched_keys = {(item.code, item.path) for item in matched}
        unused = sorted(requested - matched_keys)
        if unused:
            rendered = ", ".join(f"{code}:{path}" for code, path in unused)
            raise ArchitectureError(
                "invalid_exception",
                f"exception entries must match current findings exactly: {rendered}",
            )
        return (
            active,
            matched,
            {
                "candidate": candidate,
                "expires_on": payload["expires_on"],
                "owner": payload["owner"],
                "path": exception_path,
                "reason": payload["reason"],
                "removal_condition": payload["removal_condition"],
                "status": "applied",
            },
        )


def _parse_changes(raw: bytes) -> tuple[_ChangedPath, ...]:
    tokens = iter(part.decode("utf-8") for part in raw.split(b"\0") if part)
    changes: list[_ChangedPath] = []
    try:
        for token in tokens:
            if "\t" in token:
                status, first = token.split("\t", 1)
            else:
                status, first = token, next(tokens)
            kind = status[0]
            if kind in {"R", "C"}:
                changes.append(_ChangedPath(status, first, next(tokens)))
            elif kind == "A":
                changes.append(_ChangedPath(status, None, first))
            elif kind == "D":
                changes.append(_ChangedPath(status, first, None))
            else:
                changes.append(_ChangedPath(status, first, first))
    except (StopIteration, UnicodeDecodeError) as exc:
        raise ArchitectureError("git_output_invalid", "malformed git --name-status output") from exc
    return tuple(sorted(changes, key=lambda item: (item.path, item.old_path or "", item.status)))


def _load_json_object(raw: str | None, *, path: str, error_code: str) -> dict[str, Any]:
    if raw is None:
        raise ArchitectureError(error_code, f"required JSON object is unavailable: {path}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ArchitectureError(error_code, f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicates)
    except ArchitectureError:
        raise
    except json.JSONDecodeError as exc:
        raise ArchitectureError(error_code, f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ArchitectureError(error_code, f"{path} must contain one JSON object")
    return payload


def _validate_policy_schema(schema: dict[str, Any]) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ArchitectureError("invalid_policy_schema", "policy schema must use JSON Schema 2020-12")
    if schema.get("$id") != POLICY_SCHEMA_ID:
        raise ArchitectureError("invalid_policy_schema", "policy schema has the wrong canonical $id")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ArchitectureError("invalid_policy_schema", "policy schema root must be a closed object")
    if set(schema.get("required", [])) != POLICY_KEYS:
        raise ArchitectureError("invalid_policy_schema", "policy schema required keys do not match v1")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != POLICY_KEYS:
        raise ArchitectureError("invalid_policy_schema", "policy schema properties do not match v1")
    definitions = schema.get("$defs")
    required_definitions = {
        "exceptionContract",
        "adapterConstructor",
        "facade",
        "governedSymbol",
        "hotFile",
        "layerPolicy",
        "migrationBridge",
        "legacyApiCutover",
        "legacyApiRewrite",
        "legacyRemovedImport",
        "registry",
        "repositoryPath",
        "selectorOwner",
    }
    if not isinstance(definitions, dict) or not required_definitions <= set(definitions):
        raise ArchitectureError("invalid_policy_schema", "policy schema is missing required v1 definitions")


def _validate_json_schema_instance(instance: Any, schema: dict[str, Any]) -> None:
    """Apply the deliberately small JSON-Schema subset used by policy v1."""

    def resolve(node: dict[str, Any]) -> dict[str, Any]:
        reference = node.get("$ref")
        if reference is None:
            return node
        prefix = "#/$defs/"
        if not isinstance(reference, str) or not reference.startswith(prefix):
            raise ArchitectureError("invalid_policy_schema", f"unsupported policy schema reference: {reference}")
        target = schema.get("$defs", {}).get(reference[len(prefix) :])
        if not isinstance(target, dict):
            raise ArchitectureError("invalid_policy_schema", f"unresolved policy schema reference: {reference}")
        return target

    def visit(value: Any, node: dict[str, Any], location: str) -> None:
        node = resolve(node)
        if "const" in node and value != node["const"]:
            raise ArchitectureError("invalid_policy", f"{location} does not match the schema constant")
        if "enum" in node and value not in node["enum"]:
            raise ArchitectureError("invalid_policy", f"{location} is not an allowed schema value")
        expected_type = node.get("type")
        if expected_type == "object":
            if not isinstance(value, dict):
                raise ArchitectureError("invalid_policy", f"{location} must be an object")
            required = node.get("required", [])
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                raise ArchitectureError("invalid_policy_schema", f"{location} has an invalid required contract")
            missing = sorted(set(required) - set(value))
            if missing:
                raise ArchitectureError("invalid_policy", f"{location} is missing schema keys: {', '.join(missing)}")
            properties = node.get("properties", {})
            if not isinstance(properties, dict):
                raise ArchitectureError("invalid_policy_schema", f"{location} properties must be an object")
            if node.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                if extra:
                    raise ArchitectureError("invalid_policy", f"{location} has unknown schema keys: {', '.join(extra)}")
            for key, child in value.items():
                child_schema = properties.get(key)
                if child_schema is not None:
                    if not isinstance(child_schema, dict):
                        raise ArchitectureError("invalid_policy_schema", f"{location}.{key} schema must be an object")
                    visit(child, child_schema, f"{location}.{key}")
        elif expected_type == "array":
            if not isinstance(value, list):
                raise ArchitectureError("invalid_policy", f"{location} must be an array")
            minimum = node.get("minItems")
            if minimum is not None and (not isinstance(minimum, int) or len(value) < minimum):
                raise ArchitectureError("invalid_policy", f"{location} has too few items")
            if node.get("uniqueItems") is True:
                encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
                if len(encoded) != len(set(encoded)):
                    raise ArchitectureError("invalid_policy", f"{location} items must be unique")
            item_schema = node.get("items")
            if item_schema is not None:
                if not isinstance(item_schema, dict):
                    raise ArchitectureError("invalid_policy_schema", f"{location} item schema must be an object")
                for index, item in enumerate(value):
                    visit(item, item_schema, f"{location}[{index}]")
        elif expected_type == "string":
            if not isinstance(value, str):
                raise ArchitectureError("invalid_policy", f"{location} must be a string")
            minimum = node.get("minLength")
            if minimum is not None and (not isinstance(minimum, int) or len(value) < minimum):
                raise ArchitectureError("invalid_policy", f"{location} is shorter than the schema minimum")
            pattern = node.get("pattern")
            if pattern is not None:
                if not isinstance(pattern, str) or pattern not in SUPPORTED_SCHEMA_PATTERNS:
                    raise ArchitectureError(
                        "invalid_policy_schema",
                        f"unsupported pattern for {location}",
                    )
                try:
                    matched = re.fullmatch(pattern, value)
                except re.error as exc:
                    raise ArchitectureError("invalid_policy_schema", f"invalid pattern for {location}") from exc
                if matched is None:
                    raise ArchitectureError("invalid_policy", f"{location} does not match the schema pattern")
        elif expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ArchitectureError("invalid_policy", f"{location} must be an integer")
            if "minimum" in node and value < node["minimum"]:
                raise ArchitectureError("invalid_policy", f"{location} is below the schema minimum")
            if "maximum" in node and value > node["maximum"]:
                raise ArchitectureError("invalid_policy", f"{location} exceeds the schema maximum")
        elif expected_type == "boolean":
            if not isinstance(value, bool):
                raise ArchitectureError("invalid_policy", f"{location} must be boolean")
        elif expected_type is not None:
            raise ArchitectureError("invalid_policy_schema", f"unsupported schema type at {location}: {expected_type}")

    visit(instance, schema, "policy")


def _validate_policy(policy: dict[str, Any], git: _GitObjects, authority: str) -> None:
    if set(policy) != POLICY_KEYS:
        raise ArchitectureError("invalid_policy", "architecture policy keys must match schema v1 exactly")
    if policy["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ArchitectureError("invalid_policy", f"schema_version must equal {POLICY_SCHEMA_VERSION}")
    _require_nonempty(policy, "owner", "invalid_policy")
    _require_nonempty(policy, "reason", "invalid_policy")
    _require_path(policy.get("source_contract"), "source_contract", "invalid_policy")
    if git.blob(authority, policy["source_contract"], required=False) is None:
        raise ArchitectureError("invalid_policy", "source_contract is absent from the authority commit")
    if policy["app_root"] != "app":
        raise ArchitectureError("invalid_policy", "app_root must equal app")
    target_packages = _unique_name_list(policy["target_packages"], "target_packages")
    bounded_contexts = _unique_name_list(policy["bounded_contexts"], "bounded_contexts")
    if set(target_packages) != set(bounded_contexts) | {"bootstrap", "compat", "kernel", "platform"}:
        raise ArchitectureError("invalid_policy", "target_packages must be bounded contexts plus bootstrap/compat/kernel/platform")
    public_modules = _unique_name_list(
        policy["public_cross_domain_modules"], "public_cross_domain_modules"
    )
    if set(public_modules) != {"api", "events"}:
        raise ArchitectureError("invalid_policy", "public_cross_domain_modules must be api and events")
    kernel_modules = policy["public_kernel_modules"]
    if not isinstance(kernel_modules, list) or any(
        not isinstance(item, str) or MODULE_NAME.fullmatch(item) is None for item in kernel_modules
    ):
        raise ArchitectureError("invalid_policy", "public_kernel_modules must contain lower-snake-case modules")
    if kernel_modules != sorted(set(kernel_modules)):
        raise ArchitectureError("invalid_policy", "public_kernel_modules must be sorted and unique")
    approved = _unique_path_list(policy["approved_root_modules"], "approved_root_modules")
    if any(len(PurePosixPath(path).parts) != 2 or not path.startswith("app/") for path in approved):
        raise ArchitectureError("invalid_policy", "approved_root_modules must contain exact app-root paths")
    authority_root_modules = sorted(
        path
        for path in git.paths(authority, "app")
        if len(PurePosixPath(path).parts) == 2 and PurePosixPath(path).suffix == ".py"
    )
    if approved != authority_root_modules:
        raise ArchitectureError(
            "invalid_policy",
            "approved_root_modules must exactly inventory authority app-root Python modules",
        )
    _unique_name_list(policy["forbidden_module_names"], "forbidden_module_names")
    _unique_name_list(policy["forbidden_delivery_tokens"], "forbidden_delivery_tokens")

    layers = policy["layers"]
    if not isinstance(layers, dict) or set(layers) != set(LAYER_NAMES):
        raise ArchitectureError("invalid_policy", "layers must define application/domain/infrastructure/transport")
    for name, entry in layers.items():
        _require_exact_keys(
            entry,
            {
                "owner",
                "reason",
                "may_import_own_layers",
                "may_import_platform",
                "allow_third_party",
                "allowed_third_party_prefixes",
            },
            f"layers.{name}",
            "invalid_policy",
        )
        _require_nonempty(entry, "owner", "invalid_policy")
        _require_nonempty(entry, "reason", "invalid_policy")
        allowed = _unique_name_list(entry["may_import_own_layers"], f"layers.{name}.may_import_own_layers")
        if not set(allowed) <= set(LAYER_NAMES):
            raise ArchitectureError("invalid_policy", f"layers.{name} contains an unknown layer")
        if not isinstance(entry["may_import_platform"], bool):
            raise ArchitectureError("invalid_policy", f"layers.{name}.may_import_platform must be boolean")
        if not isinstance(entry["allow_third_party"], bool):
            raise ArchitectureError("invalid_policy", f"layers.{name}.allow_third_party must be boolean")
        prefixes = entry["allowed_third_party_prefixes"]
        if not isinstance(prefixes, list) or any(
            not isinstance(item, str) or MODULE_NAME.fullmatch(item) is None for item in prefixes
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"layers.{name}.allowed_third_party_prefixes must contain module prefixes",
            )
        if prefixes != sorted(set(prefixes)):
            raise ArchitectureError(
                "invalid_policy",
                f"layers.{name}.allowed_third_party_prefixes must be sorted and unique",
            )

    _validate_owned_entries(
        policy["frozen_hot_files"],
        keys={"path", "max_lines", "owner", "reason"},
        label="frozen_hot_files",
        git=git,
        authority=authority,
        extra_validator=_validate_hot_file,
    )
    _validate_owned_entries(
        policy["compatibility_facades"],
        keys={"path", "canonical_prefix", "max_lines", "shape", "owner", "reason"},
        label="compatibility_facades",
        git=git,
        authority=authority,
        extra_validator=_validate_facade_entry,
    )
    _validate_migration_bridges(
        policy["migration_bridges"],
        bounded_contexts=set(bounded_contexts),
        git=git,
        authority=authority,
    )
    _validate_legacy_api_cutovers(
        policy["legacy_api_cutovers"],
        bounded_contexts=set(bounded_contexts),
        public_modules=set(public_modules),
        migration_bridges=policy["migration_bridges"],
        git=git,
        authority=authority,
    )
    _validate_owned_entries(
        policy["production_registries"],
        keys={
            "path",
            "factory_function",
            "allowed_keys",
            "adapter_constructors",
            "selector_owners",
            "test_double_terms",
            "owner",
            "reason",
        },
        label="production_registries",
        git=git,
        authority=authority,
        extra_validator=_validate_registry_entry,
    )
    for registry in policy["production_registries"]:
        for selector in registry["selector_owners"]:
            if git.blob(authority, selector["path"], required=False) is None:
                raise ArchitectureError(
                    "invalid_policy",
                    f"registry selector owner is absent from authority: {selector['path']}",
                )
    _validate_owned_entries(
        policy["governed_symbols"],
        keys={"name", "path", "owner", "reason"},
        label="governed_symbols",
        git=git,
        authority=authority,
        extra_validator=_validate_symbol_entry,
        unique_field="name",
    )
    _validate_exception_contract(policy["exception_contract"])


def _validate_owned_entries(
    value: Any,
    *,
    keys: set[str],
    label: str,
    git: _GitObjects,
    authority: str,
    extra_validator: Any,
    unique_field: str = "path",
) -> None:
    if not isinstance(value, list) or not value:
        raise ArchitectureError("invalid_policy", f"{label} must be a non-empty list")
    seen: set[str] = set()
    for index, entry in enumerate(value):
        item_label = f"{label}[{index}]"
        _require_exact_keys(entry, keys, item_label, "invalid_policy")
        _require_nonempty(entry, "owner", "invalid_policy")
        _require_nonempty(entry, "reason", "invalid_policy")
        extra_validator(entry, item_label)
        identity = entry[unique_field]
        if identity in seen:
            raise ArchitectureError("invalid_policy", f"duplicate {label} identity: {identity}")
        seen.add(identity)
        path = entry["path"]
        if git.blob(authority, path, required=False) is None:
            raise ArchitectureError("invalid_policy", f"policy path is absent from authority: {path}")
    if [entry[unique_field] for entry in value] != sorted(seen):
        raise ArchitectureError("invalid_policy", f"{label} must be sorted by {unique_field}")


def _validate_hot_file(entry: dict[str, Any], label: str) -> None:
    _require_path(entry["path"], f"{label}.path", "invalid_policy")
    if not isinstance(entry["max_lines"], int) or isinstance(entry["max_lines"], bool) or entry["max_lines"] < 1:
        raise ArchitectureError("invalid_policy", f"{label}.max_lines must be a positive integer")


def _validate_facade_entry(entry: dict[str, Any], label: str) -> None:
    _validate_hot_file(entry, label)
    if entry["shape"] != "imports_only":
        raise ArchitectureError("invalid_policy", f"{label}.shape must equal imports_only")
    prefix = entry["canonical_prefix"]
    if not isinstance(prefix, str) or not prefix.startswith("app."):
        raise ArchitectureError("invalid_policy", f"{label}.canonical_prefix must be an app module")


def _validate_migration_bridges(
    value: Any,
    *,
    bounded_contexts: set[str],
    git: _GitObjects,
    authority: str,
) -> None:
    if not isinstance(value, list):
        raise ArchitectureError("invalid_policy", "migration_bridges must be a list")
    identities: list[tuple[str, str]] = []
    source_aliases: set[tuple[str, str]] = set()
    source_symbols: set[tuple[str, str]] = set()
    for index, entry in enumerate(value):
        label = f"migration_bridges[{index}]"
        _require_exact_keys(
            entry,
            {
                "source_path",
                "target_module",
                "module_alias",
                "symbols",
                "owner",
                "reason",
                "removal_condition",
            },
            label,
            "invalid_policy",
        )
        _require_nonempty(entry, "owner", "invalid_policy")
        _require_nonempty(entry, "reason", "invalid_policy")
        _require_nonempty(entry, "removal_condition", "invalid_policy")
        source_path = entry["source_path"]
        _require_path(source_path, f"{label}.source_path", "invalid_policy")
        if (
            not source_path.startswith("app/")
            or PurePosixPath(source_path).suffix != ".py"
            or git.blob(authority, source_path, required=False) is None
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.source_path must be an authority-owned app Python module",
            )
        target_module = entry["target_module"]
        if (
            not isinstance(target_module, str)
            or not target_module.startswith("app.")
            or any(MODULE_NAME.fullmatch(part) is None for part in target_module.split(".")[1:])
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.target_module must be an exact app module",
            )
        target_parts = target_module.split(".")
        bounded_context_infrastructure = (
            len(target_parts) >= 4
            and target_parts[1] in bounded_contexts
            and target_parts[2] == "infrastructure"
        )
        platform_technical_module = target_module in ALLOWED_PLATFORM_MIGRATION_TARGETS
        if not (bounded_context_infrastructure or platform_technical_module):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.target_module must name bounded-context infrastructure or a platform technical module",
            )
        module_alias = entry["module_alias"]
        if not isinstance(module_alias, str) or re.fullmatch(
            r"_?[a-z][a-z0-9_]*",
            module_alias,
        ) is None:
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.module_alias must be lower-snake-case",
            )
        source_alias = (source_path, module_alias)
        if source_alias in source_aliases:
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.module_alias must be unique within one source_path",
            )
        source_aliases.add(source_alias)
        symbols = entry["symbols"]
        if not isinstance(symbols, list) or not symbols or any(
            not isinstance(symbol, str)
            or re.fullmatch(r"_?[A-Za-z][A-Za-z0-9_]*", symbol) is None
            or keyword.iskeyword(symbol)
            for symbol in symbols
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.symbols must be non-empty ASCII Python identifiers",
            )
        if symbols != sorted(set(symbols)):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.symbols must be sorted and unique",
            )
        duplicate_source_symbols = {
            symbol for symbol in symbols if (source_path, symbol) in source_symbols
        }
        if duplicate_source_symbols:
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.symbols must be unique across bridges sharing one source_path",
            )
        source_symbols.update((source_path, symbol) for symbol in symbols)
        identities.append((source_path, target_module))
    if identities != sorted(set(identities)):
        raise ArchitectureError(
            "invalid_policy",
            "migration_bridges must be sorted and unique by source_path and target_module",
        )


def _validate_legacy_api_cutovers(
    value: Any,
    *,
    bounded_contexts: set[str],
    public_modules: set[str],
    migration_bridges: Sequence[dict[str, Any]],
    git: _GitObjects,
    authority: str,
) -> None:
    if not isinstance(value, list):
        raise ArchitectureError("invalid_policy", "legacy_api_cutovers must be a list")
    identities: list[tuple[str, str]] = []
    bridge_aliases = {
        (bridge["source_path"], bridge["module_alias"])
        for bridge in migration_bridges
    }
    bridge_sources = {bridge["source_path"] for bridge in migration_bridges}
    for index, entry in enumerate(value):
        label = f"legacy_api_cutovers[{index}]"
        _require_exact_keys(
            entry,
            {
                "source_path",
                "public_module",
                "canonical_module",
                "module_alias",
                "removed_imports",
                "rewrites",
                "owner",
                "reason",
                "removal_condition",
            },
            label,
            "invalid_policy",
        )
        for key in ("owner", "reason", "removal_condition"):
            _require_nonempty(entry, key, "invalid_policy")
        source_path = _require_path(
            entry["source_path"], f"{label}.source_path", "invalid_policy"
        )
        source = git.text(authority, source_path, required=False)
        if (
            not source_path.startswith("app/")
            or PurePosixPath(source_path).suffix != ".py"
            or source is None
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.source_path must be an authority-owned app Python module",
            )
        if source_path not in bridge_sources:
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.source_path must already be frozen by a migration bridge",
            )
        public_module = entry["public_module"]
        if not isinstance(public_module, str):
            raise ArchitectureError(
                "invalid_policy", f"{label}.public_module must be an exact app module"
            )
        public_parts = public_module.split(".")
        if (
            len(public_parts) != 3
            or public_parts[0] != "app"
            or public_parts[1] not in bounded_contexts
            or public_parts[2] not in public_modules
            or any(MODULE_NAME.fullmatch(part) is None for part in public_parts[1:])
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.public_module must name one bounded-context api.py or events.py",
            )
        canonical_module = entry["canonical_module"]
        if not isinstance(canonical_module, str):
            raise ArchitectureError(
                "invalid_policy", f"{label}.canonical_module must be an exact app module"
            )
        canonical_parts = canonical_module.split(".")
        if (
            len(canonical_parts) < 4
            or canonical_parts[:2] != public_parts[:2]
            or canonical_parts[2] not in {"application", "domain"}
            or any(MODULE_NAME.fullmatch(part) is None for part in canonical_parts[1:])
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.canonical_module must name application/domain code in the public module owner",
            )
        module_alias = entry["module_alias"]
        if not isinstance(module_alias, str) or re.fullmatch(
            r"_?[a-z][a-z0-9_]*", module_alias
        ) is None:
            raise ArchitectureError(
                "invalid_policy", f"{label}.module_alias must be lower-snake-case"
            )
        if (source_path, module_alias) in bridge_aliases:
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.module_alias cannot reuse a migration bridge alias",
            )
        removed_imports = entry["removed_imports"]
        if not isinstance(removed_imports, list):
            raise ArchitectureError(
                "invalid_policy", f"{label}.removed_imports must be a list"
            )
        import_identities: list[tuple[str, str]] = []
        for removed in removed_imports:
            _require_exact_keys(
                removed,
                {"module", "name"},
                f"{label}.removed_imports",
                "invalid_policy",
            )
            module = removed["module"]
            name = removed["name"]
            if (
                not isinstance(module, str)
                or MODULE_NAME.fullmatch(module) is None
                or module not in sys.stdlib_module_names
                or not _is_python_identifier(name)
            ):
                raise ArchitectureError(
                    "invalid_policy",
                    f"{label}.removed_imports must name exact standard-library imports",
                )
            import_identities.append((module, name))
        if import_identities != sorted(set(import_identities)):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.removed_imports must be sorted and unique",
            )
        rewrites = entry["rewrites"]
        if not isinstance(rewrites, list) or not rewrites:
            raise ArchitectureError(
                "invalid_policy", f"{label}.rewrites must be a non-empty list"
            )
        rewrite_identities: list[tuple[str, str]] = []
        old_symbols: set[str] = set()
        new_symbols: set[str] = set()
        for rewrite in rewrites:
            _require_exact_keys(
                rewrite,
                {"old_symbol", "new_symbol"},
                f"{label}.rewrites",
                "invalid_policy",
            )
            old_symbol = rewrite["old_symbol"]
            new_symbol = rewrite["new_symbol"]
            if not _is_python_identifier(old_symbol) or not _is_python_identifier(new_symbol):
                raise ArchitectureError(
                    "invalid_policy",
                    f"{label}.rewrites must contain ASCII Python identifiers",
                )
            rewrite_identities.append((old_symbol, new_symbol))
            old_symbols.add(old_symbol)
            new_symbols.add(new_symbol)
        if (
            rewrite_identities != sorted(set(rewrite_identities))
            or len(old_symbols) != len(rewrites)
            or len(new_symbols) != len(rewrites)
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.rewrites must be sorted, unique, and one-to-one",
            )
        source_tree = _parse_python(source, source_path, candidate=False)
        state = _legacy_api_cutover_state(source_tree, entry)
        if state == "invalid":
            raise ArchitectureError(
                "invalid_policy",
                f"{label} source must be entirely pending or consumed",
            )
        if state == "consumed":
            target_path = _module_python_path(public_module)
            target_source = git.text(authority, target_path, required=False)
            if target_source is None:
                raise ArchitectureError(
                    "invalid_policy",
                    f"{label} consumed public module is absent from authority",
                )
            target_tree = _parse_python(target_source, target_path, candidate=False)
            target_issues = _public_cutover_target_issues(target_tree, entry)
            if target_issues:
                raise ArchitectureError(
                    "invalid_policy",
                    f"{label} consumed public module violates the identity boundary: {', '.join(target_issues)}",
                )
            canonical_path = _module_python_path(canonical_module)
            canonical_source = git.text(authority, canonical_path, required=False)
            if canonical_source is None:
                raise ArchitectureError(
                    "invalid_policy",
                    f"{label} consumed canonical module is absent from authority",
                )
            canonical_tree = _parse_python(
                canonical_source,
                canonical_path,
                candidate=False,
            )
            canonical_issues = _canonical_cutover_owner_issues(canonical_tree, entry)
            if canonical_issues:
                raise ArchitectureError(
                    "invalid_policy",
                    f"{label} consumed canonical module does not own every declared symbol: "
                    + ", ".join(canonical_issues),
                )
        identities.append((source_path, public_module))
    if identities != sorted(set(identities)):
        raise ArchitectureError(
            "invalid_policy",
            "legacy_api_cutovers must be sorted and unique by source_path and public_module",
        )
    if len({source_path for source_path, _module in identities}) != len(identities):
        raise ArchitectureError(
            "invalid_policy", "only one legacy API cutover may govern one source_path"
        )


def _validate_registry_entry(entry: dict[str, Any], label: str) -> None:
    _require_path(entry["path"], f"{label}.path", "invalid_policy")
    if not isinstance(entry["factory_function"], str) or re.fullmatch(
        r"_?[a-z][a-z0-9_]*",
        entry["factory_function"],
    ) is None:
        raise ArchitectureError("invalid_policy", f"{label}.factory_function must be lower-snake-case")
    allowed = entry["allowed_keys"]
    if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) and item for item in allowed):
        raise ArchitectureError("invalid_policy", f"{label}.allowed_keys must be non-empty strings")
    if len(allowed) != len(set(allowed)) or allowed != sorted(allowed):
        raise ArchitectureError("invalid_policy", f"{label}.allowed_keys must be sorted and unique")
    constructors = entry["adapter_constructors"]
    if not isinstance(constructors, list) or not constructors:
        raise ArchitectureError(
            "invalid_policy",
            f"{label}.adapter_constructors must be a non-empty list",
        )
    constructor_identities: list[tuple[str, str]] = []
    for constructor in constructors:
        _require_exact_keys(
            constructor,
            {"module", "name"},
            f"{label}.adapter_constructors",
            "invalid_policy",
        )
        module = constructor["module"]
        name = constructor["name"]
        if not isinstance(module, str) or not module.startswith("app.") or any(
            MODULE_NAME.fullmatch(part) is None for part in module.split(".")[1:]
        ):
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.adapter_constructors.module must be an app module",
            )
        if not isinstance(name, str) or re.fullmatch(r"[A-Z][A-Za-z0-9]*", name) is None:
            raise ArchitectureError(
                "invalid_policy",
                f"{label}.adapter_constructors.name must be a class name",
            )
        constructor_identities.append((module, name))
    if constructor_identities != sorted(set(constructor_identities)):
        raise ArchitectureError(
            "invalid_policy",
            f"{label}.adapter_constructors must be sorted and unique",
        )
    selector_owners = entry["selector_owners"]
    if not isinstance(selector_owners, list) or not selector_owners:
        raise ArchitectureError("invalid_policy", f"{label}.selector_owners must be a non-empty list")
    selector_identities: list[tuple[str, str]] = []
    for selector in selector_owners:
        _require_exact_keys(
            selector,
            {"path", "symbol"},
            f"{label}.selector_owners",
            "invalid_policy",
        )
        path = _require_path(selector["path"], f"{label}.selector_owners.path", "invalid_policy")
        symbol = selector["symbol"]
        if not isinstance(symbol, str) or SYMBOL_NAME.fullmatch(symbol) is None:
            raise ArchitectureError("invalid_policy", f"{label}.selector_owners.symbol is invalid")
        selector_identities.append((path, symbol))
    if selector_identities != sorted(set(selector_identities)):
        raise ArchitectureError("invalid_policy", f"{label}.selector_owners must be sorted and unique")
    terms = entry["test_double_terms"]
    if not isinstance(terms, list) or not terms or not all(isinstance(item, str) and item for item in terms):
        raise ArchitectureError("invalid_policy", f"{label}.test_double_terms must be non-empty strings")
    if terms != sorted(set(terms)):
        raise ArchitectureError("invalid_policy", f"{label}.test_double_terms must be sorted and unique")


def _validate_symbol_entry(entry: dict[str, Any], label: str) -> None:
    _require_path(entry["path"], f"{label}.path", "invalid_policy")
    if not isinstance(entry["name"], str) or SYMBOL_NAME.fullmatch(entry["name"]) is None:
        raise ArchitectureError("invalid_policy", f"{label}.name must be an upper-snake-case symbol")


def _validate_exception_contract(contract: Any) -> None:
    expected = {"path", "schema_version", "max_days", "non_exemptible_codes", "owner", "reason"}
    _require_exact_keys(contract, expected, "exception_contract", "invalid_policy")
    if contract["path"] != EXCEPTION_PATH:
        raise ArchitectureError("invalid_policy", "exception_contract.path is not canonical")
    if contract["schema_version"] != "ai-platform.architecture-governance-exception.v1":
        raise ArchitectureError("invalid_policy", "exception_contract.schema_version is invalid")
    if not isinstance(contract["max_days"], int) or isinstance(contract["max_days"], bool) or not 1 <= contract["max_days"] <= 90:
        raise ArchitectureError("invalid_policy", "exception_contract.max_days must be between 1 and 90")
    codes = contract["non_exemptible_codes"]
    if not isinstance(codes, list) or codes != sorted(set(codes)) or not BUILTIN_NON_EXEMPTIBLE_CODES <= set(codes):
        raise ArchitectureError("invalid_policy", "exception non-exemptible codes must include the v1 authority set")
    _require_nonempty(contract, "owner", "invalid_policy")
    _require_nonempty(contract, "reason", "invalid_policy")


def _validate_exception(
    payload: dict[str, Any],
    *,
    contract: dict[str, Any],
    today: date,
    authority_ref: str,
    base_ref: str,
    scope_sha256: str,
) -> None:
    expected = {
        "schema_version",
        "candidate",
        "expires_on",
        "owner",
        "reason",
        "removal_condition",
        "paths",
        "violations",
    }
    _require_exact_keys(payload, expected, "exception", "invalid_exception")
    if payload["schema_version"] != contract["schema_version"]:
        raise ArchitectureError("invalid_exception", "exception schema_version is invalid")
    for key in ("owner", "reason", "removal_condition"):
        _require_nonempty(payload, key, "invalid_exception")
    expiry = _parse_date(payload["expires_on"], "expires_on")
    if expiry < today:
        raise ArchitectureError("invalid_exception", "architecture exception has expired")
    if expiry > today + timedelta(days=contract["max_days"]):
        raise ArchitectureError("invalid_exception", "architecture exception exceeds the policy maximum duration")
    candidate = payload["candidate"]
    _require_exact_keys(
        candidate,
        {"authority_ref", "base_ref", "head_scope_sha256"},
        "exception.candidate",
        "invalid_exception",
    )
    for key in ("authority_ref", "base_ref"):
        if not isinstance(candidate[key], str) or FULL_SHA.fullmatch(candidate[key]) is None:
            raise ArchitectureError("invalid_exception", f"exception candidate {key} must be a full lowercase SHA")
    if not isinstance(candidate["head_scope_sha256"], str) or SHA256_HEX.fullmatch(candidate["head_scope_sha256"]) is None:
        raise ArchitectureError("invalid_exception", "head_scope_sha256 must be a lowercase SHA-256 digest")
    if candidate["authority_ref"] != authority_ref:
        raise ArchitectureError("invalid_exception", "exception authority_ref does not match the evaluator authority")
    if candidate["base_ref"] != base_ref or candidate["head_scope_sha256"] != scope_sha256:
        raise ArchitectureError("invalid_exception", "exception candidate binding does not match the exact patch")
    paths = _unique_path_list(payload["paths"], "exception.paths", error_code="invalid_exception")
    entries = payload["violations"]
    if not isinstance(entries, list) or not entries:
        raise ArchitectureError("invalid_exception", "exception violations must be a non-empty list")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        _require_exact_keys(entry, {"code", "path"}, "exception violation", "invalid_exception")
        _require_nonempty(entry, "code", "invalid_exception")
        _require_path(entry["path"], "exception violation path", "invalid_exception")
        key = (entry["code"], entry["path"])
        if key in seen:
            raise ArchitectureError("invalid_exception", "exception violation entries must be unique")
        seen.add(key)
    if set(paths) != {path for _code, path in seen}:
        raise ArchitectureError("invalid_exception", "exception paths must exactly match violation paths")


def _parse_python(source: str, path: str, *, candidate: bool) -> ast.Module:
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        code = "candidate_python_syntax" if candidate else "base_python_syntax"
        raise ArchitectureError(code, f"cannot parse {path}:{exc.lineno}: {exc.msg}") from exc


def _is_python_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"_?[A-Za-z][A-Za-z0-9_]*", value) is not None
        and not keyword.iskeyword(value)
    )


def _module_python_path(module: str) -> str:
    return f"{module.replace('.', '/')}.py"


def _top_level_bound_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_assignment_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_assignment_target_names(node.target))
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return names


def _top_level_local_binding_counts(tree: ast.Module) -> Counter[str]:
    counts: Counter[str] = Counter()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name] += 1
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                counts.update(_assignment_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            counts.update(_assignment_target_names(node.target))
    return counts


def _top_level_import_bound_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return names


def _is_legacy_api_cutover_import(node: ast.stmt, entry: dict[str, Any]) -> bool:
    return (
        isinstance(node, ast.Import)
        and len(node.names) == 1
        and node.names[0].name == entry["public_module"]
        and node.names[0].asname == entry["module_alias"]
    )


def _legacy_api_cutover_import_count(tree: ast.Module, entry: dict[str, Any]) -> int:
    return sum(1 for node in tree.body if _is_legacy_api_cutover_import(node, entry))


def _is_declared_removed_import(node: ast.stmt, entry: dict[str, Any]) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.level == 0
        and len(node.names) == 1
        and node.names[0].asname is None
        and any(
            node.module == removed["module"] and node.names[0].name == removed["name"]
            for removed in entry["removed_imports"]
        )
    )


def _removed_import_identities(tree: ast.Module, entry: dict[str, Any]) -> Counter[tuple[str, str]]:
    declared = {
        (removed["module"], removed["name"])
        for removed in entry["removed_imports"]
    }
    return Counter(
        (node.module or "", node.names[0].name)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and len(node.names) == 1
        and node.names[0].asname is None
        and (node.module or "", node.names[0].name) in declared
    )


def _legacy_api_cutover_state(tree: ast.Module, entry: dict[str, Any]) -> str:
    old_symbols = {rewrite["old_symbol"] for rewrite in entry["rewrites"]}
    bindings = _top_level_local_binding_counts(tree)
    imported_names = _top_level_import_bound_names(tree)
    import_count = _legacy_api_cutover_import_count(tree, entry)
    removed_imports = _removed_import_identities(tree, entry)
    expected_removed = Counter(
        (removed["module"], removed["name"])
        for removed in entry["removed_imports"]
    )
    if (
        import_count == 0
        and _exact_legacy_definition_symbols(tree, old_symbols) == old_symbols
        and all(bindings[symbol] == 1 for symbol in old_symbols)
        and not old_symbols & imported_names
        and removed_imports == expected_removed
    ):
        return "pending"
    if (
        import_count == 1
        and not old_symbols & _top_level_bound_names(tree)
        and not removed_imports
        and not _legacy_api_cutover_reference_issues(tree, entry)
    ):
        return "consumed"
    return "invalid"


def _exact_legacy_definition_symbols(
    tree: ast.Module,
    old_symbols: set[str],
) -> set[str]:
    exact: set[str] = set()
    for node in tree.body:
        bindings = _top_level_node_binding_names(node)
        overlap = bindings & old_symbols
        if not overlap:
            continue
        if len(bindings) == 1 and len(overlap) == 1:
            exact.update(overlap)
    return exact


def _legacy_api_cutover_attempted(
    base_tree: ast.Module,
    head_tree: ast.Module,
    entry: dict[str, Any],
) -> bool:
    if _legacy_api_cutover_import_count(head_tree, entry):
        return True
    if any(
        isinstance(node, ast.Import)
        and any(alias.name == entry["public_module"] for alias in node.names)
        for node in head_tree.body
    ):
        return True
    old_symbols = {rewrite["old_symbol"] for rewrite in entry["rewrites"]}
    return (
        _legacy_api_definition_fingerprints(base_tree, old_symbols)
        != _legacy_api_definition_fingerprints(head_tree, old_symbols)
        or _removed_import_identities(base_tree, entry)
        != _removed_import_identities(head_tree, entry)
    )


def _legacy_api_definition_fingerprints(
    tree: ast.Module,
    symbols: set[str],
) -> Counter[str]:
    return Counter(
        ast.dump(node, include_attributes=False)
        for node in tree.body
        if _top_level_node_binding_names(node) & symbols
    )


def _legacy_api_cutover_reference_issues(
    tree: ast.Module,
    entry: dict[str, Any],
) -> list[str]:
    old_symbols = {rewrite["old_symbol"] for rewrite in entry["rewrites"]}
    allowed_new = {rewrite["new_symbol"] for rewrite in entry["rewrites"]}
    alias = entry["module_alias"]
    attributes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == alias
    ]
    issues: list[str] = []
    old_uses = sorted(
        {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in old_symbols}
    )
    if old_uses:
        issues.append(f"legacy symbol uses remain: {', '.join(old_uses)}")
    old_imports = sorted(old_symbols & _top_level_import_bound_names(tree))
    if old_imports:
        issues.append(f"legacy import bindings remain: {', '.join(old_imports)}")
    used_new = {node.attr for node in attributes}
    undeclared = sorted(used_new - allowed_new)
    missing = sorted(allowed_new - used_new)
    if undeclared:
        issues.append(f"undeclared public symbols used: {', '.join(undeclared)}")
    if missing:
        issues.append(f"declared public symbols unused: {', '.join(missing)}")
    bare_alias_uses = sum(
        1 for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == alias
    ) - len(attributes)
    if bare_alias_uses:
        issues.append("public module alias is used outside declared attributes")
    assignments = _assignment_names(tree)
    if alias in assignments:
        issues.append("public module alias is rebound")
    return issues


def _public_cutover_target_issues(
    tree: ast.Module,
    entry: dict[str, Any],
) -> list[str]:
    expected = {rewrite["new_symbol"] for rewrite in entry["rewrites"]}
    imported: list[str] = []
    issues: list[str] = []
    for index, node in enumerate(tree.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == entry["canonical_module"]
            and all(
                alias.name != "*" and alias.asname == alias.name
                for alias in node.names
            )
        ):
            imported.extend(alias.name for alias in node.names)
            continue
        issues.append(f"unexpected top-level {type(node).__name__}")
    if imported != sorted(expected) or len(imported) != len(set(imported)):
        issues.append(
            "public boundary imports must be exact same-name re-exports of sorted declared symbols"
        )
    return sorted(set(issues))


def _canonical_cutover_owner_issues(
    tree: ast.Module,
    entry: dict[str, Any],
) -> list[str]:
    expected = {rewrite["new_symbol"] for rewrite in entry["rewrites"]}
    local_counts = _top_level_local_binding_counts(tree)
    exact_definitions = _exact_legacy_definition_symbols(tree, expected)
    imported_names = _top_level_import_bound_names(tree)
    issues: list[str] = []
    missing = sorted(symbol for symbol in expected if local_counts[symbol] != 1)
    if missing:
        issues.append(f"canonical definitions must exist exactly once: {', '.join(missing)}")
    non_exact = sorted(expected - exact_definitions)
    if non_exact:
        issues.append(f"canonical definitions must bind one declared symbol: {', '.join(non_exact)}")
    imported = sorted(expected & imported_names)
    if imported:
        issues.append(f"canonical symbols cannot be imported aliases: {', '.join(imported)}")
    for node in tree.body:
        bindings = _top_level_node_binding_names(node)
        if len(bindings) != 1 or not bindings <= expected:
            continue
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            value = node.value
        if value is None:
            continue
        imported_dependencies = sorted(
            {
                candidate.id
                for candidate in ast.walk(value)
                if isinstance(candidate, ast.Name) and candidate.id in imported_names
            }
        )
        if imported_dependencies:
            symbol = next(iter(bindings))
            issues.append(
                f"canonical assignment {symbol} cannot depend on imported aliases: "
                + ", ".join(imported_dependencies)
            )
        unsafe_nodes = [
            candidate
            for candidate in ast.walk(value)
            if isinstance(candidate, (ast.Attribute, ast.Lambda, ast.Subscript))
            or (
                isinstance(candidate, ast.Call)
                and not (
                    isinstance(candidate.func, ast.Name)
                    and candidate.func.id in {"dict", "frozenset", "list", "set", "tuple"}
                )
            )
        ]
        if unsafe_nodes:
            symbol = next(iter(bindings))
            issues.append(
                f"canonical assignment {symbol} must be a static value definition"
            )
    return sorted(set(issues))


def _legacy_api_cutover_target_findings(
    entry: dict[str, Any],
    *,
    git: _GitObjects,
    revision: str,
    candidate: bool,
) -> list[Finding]:
    details = {
        "source_path": entry["source_path"],
        "public_module": entry["public_module"],
        "canonical_module": entry["canonical_module"],
    }
    findings: list[Finding] = []
    target_path = _module_python_path(entry["public_module"])
    target_source = git.text(revision, target_path, required=False)
    target_issues: list[str] = []
    if target_source is not None:
        target_tree = _parse_python(target_source, target_path, candidate=candidate)
        target_issues = _public_cutover_target_issues(target_tree, entry)
    if target_source is None or target_issues:
        findings.append(
            Finding(
                "legacy_api_cutover_target_contract",
                "the public cutover module must be an exact identity boundary to the canonical owner",
                target_path,
                exemptible=False,
                details={**details, "issues": target_issues},
            )
        )

    canonical_path = _module_python_path(entry["canonical_module"])
    canonical_source = git.text(revision, canonical_path, required=False)
    canonical_issues: list[str] = []
    if canonical_source is not None:
        canonical_tree = _parse_python(
            canonical_source,
            canonical_path,
            candidate=candidate,
        )
        canonical_issues = _canonical_cutover_owner_issues(canonical_tree, entry)
    if canonical_source is None or canonical_issues:
        findings.append(
            Finding(
                "legacy_api_cutover_target_contract",
                "the canonical cutover module must locally define every declared symbol",
                canonical_path,
                exemptible=False,
                details={**details, "issues": canonical_issues},
            )
        )
    return findings


def _python_modules(paths: Sequence[str]) -> set[str]:
    return {
        _path_module(path)
        for path in paths
        if _is_python_path(path)
    }


def _import_edges(
    tree: ast.Module,
    path: str,
    *,
    known_modules: set[str] | None = None,
) -> tuple[_ImportEdge, ...]:
    package_parts = list(PurePosixPath(path).with_suffix("").parts[:-1])
    edges: list[_ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.extend(_ImportEdge(alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(package_parts) - (node.level - 1))
                prefix = package_parts[:keep]
                module_parts = node.module.split(".") if node.module else []
                target = ".".join([*prefix, *module_parts])
            else:
                target = node.module or ""
            if target and node.names and node.module is None:
                edges.extend(_ImportEdge(f"{target}.{alias.name}", node.lineno) for alias in node.names)
            elif target == "app":
                edges.extend(_ImportEdge(f"app.{alias.name}", node.lineno) for alias in node.names)
            elif target and len(target.split(".")) == 2:
                edges.extend(
                    _ImportEdge(f"{target}.{alias.name}", node.lineno)
                    for alias in node.names
                )
            elif target:
                if target.startswith("app.kernel.") and known_modules is not None:
                    edges.extend(
                        _ImportEdge(
                            candidate if candidate in known_modules else target,
                            node.lineno,
                        )
                        for alias in node.names
                        for candidate in (f"{target}.{alias.name}",)
                    )
                else:
                    edges.append(_ImportEdge(target, node.lineno))
    unique = {(edge.target, edge.line): edge for edge in edges}
    return tuple(sorted(unique.values(), key=lambda item: (item.target, item.line)))


def _module_location(module: str, policy: dict[str, Any]) -> _ModuleLocation:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != policy["app_root"]:
        return _ModuleLocation(None, None, None, None)
    package = parts[1]
    if package not in set(policy["bounded_contexts"]):
        return _ModuleLocation(package, None, package if package in {"bootstrap", "compat", "kernel", "platform"} else None, None)
    third = parts[2] if len(parts) >= 3 else None
    layer = third if third in set(policy["layers"]) else None
    boundary = third if third in set(policy["public_cross_domain_modules"]) | {"registry"} else None
    return _ModuleLocation(package, package, layer, boundary)


def _path_module(path: str) -> str:
    pure = PurePosixPath(path).with_suffix("")
    parts = list(pure.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _new_edge_finding(policy: dict[str, Any], source_path: str, edge: _ImportEdge) -> Finding | None:
    source = _module_location(_path_module(source_path), policy)
    if edge.target != "app" and not edge.target.startswith("app."):
        external_root = edge.target.split(".", 1)[0]
        if external_root in sys.stdlib_module_names:
            return None
        if source.package == "kernel":
            return Finding(
                "layer_external_dependency_forbidden",
                f"kernel cannot import third-party module '{external_root}'",
                source_path,
                edge.line,
                exemptible=False,
                details={"target": edge.target},
            )
        if source.context is not None and source.layer is None:
            return Finding(
                "layer_external_dependency_forbidden",
                "legacy or boundary domain modules cannot add third-party dependencies",
                source_path,
                edge.line,
                exemptible=False,
                details={"target": edge.target},
            )
        if source.context is None:
            if source.package in {"bootstrap", "platform"}:
                return None
            return Finding(
                "layer_external_dependency_forbidden",
                "legacy app modules cannot add third-party dependencies outside a target layer",
                source_path,
                edge.line,
                exemptible=False,
                details={"target": edge.target},
            )
        layer_policy = policy["layers"][source.layer]
        if layer_policy["allow_third_party"] or external_root in set(
            layer_policy["allowed_third_party_prefixes"]
        ):
            return None
        return Finding(
            "layer_external_dependency_forbidden",
            f"{source.layer} cannot import third-party module '{external_root}'",
            source_path,
            edge.line,
            exemptible=False,
            details={"target": edge.target},
        )
    target = _module_location(edge.target, policy)
    contexts = set(policy["bounded_contexts"])
    public = set(policy["public_cross_domain_modules"])
    non_exemptible = {"exemptible": False, "details": {"target": edge.target}}

    if source.package == "kernel" and target.package not in {None, "kernel"}:
        return Finding(
            "kernel_product_import",
            "kernel is a dependency leaf and cannot import product or platform packages",
            source_path,
            edge.line,
            **non_exemptible,
        )
    if (
        target.package == "kernel"
        and source.package != "kernel"
        and not _kernel_import_allowed(edge.target, policy)
    ):
        return Finding(
            "kernel_public_surface_forbidden",
            "kernel imports must target an explicitly governed public kernel module",
            source_path,
            edge.line,
            **non_exemptible,
        )
    if source.package == "platform" and target.package in contexts | {"bootstrap", "compat"}:
        return Finding(
            "platform_product_import",
            "platform technical clients cannot import a product context",
            source_path,
            edge.line,
            **non_exemptible,
        )
    if source.package == "bootstrap":
        return None
    if source.package == "compat":
        allowed = target.package == "kernel" or (
            target.context is not None and (target.boundary in public or target.layer == "transport")
        )
        if not allowed:
            return Finding(
                "compatibility_import_forbidden",
                "compatibility code may only delegate to canonical api/events/transport boundaries",
                source_path,
                edge.line,
                **non_exemptible,
            )
        return None

    if source.context is not None:
        if target.context is not None:
            if source.context != target.context:
                if source.layer == "domain" or target.boundary not in public:
                    return Finding(
                        "cross_domain_internal_import",
                        "cross-domain imports must target the owner's api.py or events.py boundary",
                        source_path,
                        edge.line,
                        **non_exemptible,
                    )
                return None
            if source.layer is None:
                if source.boundary in public | {"registry"}:
                    if target.layer not in {"application", "domain"}:
                        return Finding(
                            "layer_dependency_forbidden",
                            "domain boundary modules may import only their application/domain internals",
                            source_path,
                            edge.line,
                            **non_exemptible,
                        )
                    return None
                if target.boundary not in public:
                    return Finding(
                        "layer_dependency_forbidden",
                        "legacy domain modules may migrate only through their own api.py or events.py boundary",
                        source_path,
                        edge.line,
                        **non_exemptible,
                    )
                return None
            if source.layer is not None and target.boundary is not None:
                return Finding(
                    "layer_dependency_forbidden",
                    f"{source.layer} cannot import its own outward {target.boundary} boundary",
                    source_path,
                    edge.line,
                    **non_exemptible,
                )
            if source.layer is not None and target.layer is not None:
                allowed_layers = set(policy["layers"][source.layer]["may_import_own_layers"])
                if target.layer not in allowed_layers:
                    return Finding(
                        "layer_dependency_forbidden",
                        f"{source.layer} cannot import its own {target.layer} layer",
                        source_path,
                        edge.line,
                        **non_exemptible,
                    )
            return None
        if target.package == "kernel":
            return None
        if target.package == "platform" and source.layer is not None and policy["layers"][source.layer]["may_import_platform"]:
            return None
        if target.package is not None:
            return Finding(
                "layer_dependency_forbidden",
                "target domain code cannot depend on bootstrap, compat, platform, or legacy app internals",
                source_path,
                edge.line,
                **non_exemptible,
            )
        return None

    if target.context is not None and target.boundary not in public:
        return Finding(
            "cross_domain_internal_import",
            "legacy callers may migrate only through a domain api.py or events.py boundary",
            source_path,
            edge.line,
            **non_exemptible,
        )
    return None


def _kernel_import_allowed(target: str, policy: dict[str, Any]) -> bool:
    parts = target.split(".")
    return len(parts) == 3 and parts[2] in set(policy["public_kernel_modules"])


def _assignment_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(_assignment_target_names(target))
                dynamic_name = _globals_subscript_name(target)
                if dynamic_name is not None:
                    names.add(dynamic_name)
        elif isinstance(node, ast.Call) and _call_name(node.func) == "setattr" and len(node.args) >= 2:
            symbol = node.args[1]
            if isinstance(symbol, ast.Constant) and isinstance(symbol.value, str):
                names.add(symbol.value)
    return names


def _assignment_target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in target.elts:
            names.update(_assignment_target_names(element))
        return names
    return set()


def _globals_subscript_name(target: ast.expr) -> str | None:
    if not isinstance(target, ast.Subscript):
        return None
    if not isinstance(target.value, ast.Call) or _call_name(target.value.func) != "globals":
        return None
    slice_value = target.slice
    if isinstance(slice_value, ast.Constant) and isinstance(slice_value.value, str):
        return slice_value.value
    return None


def _governed_symbol_findings(
    policy: dict[str, Any],
    path: str,
    old_path: str | None,
    head_tree: ast.Module,
    git: _GitObjects,
    base: str,
) -> list[Finding]:
    head_names = _assignment_names(head_tree)
    base_names: set[str] = set()
    if old_path is not None:
        old_text = git.text(base, old_path, required=False)
        if old_text is not None:
            base_names = _assignment_names(_parse_python(old_text, old_path, candidate=False))
    findings: list[Finding] = []
    for symbol in policy["governed_symbols"]:
        name = symbol["name"]
        if name in head_names - base_names and path != symbol["path"]:
            findings.append(
                Finding(
                    "governed_symbol_owner",
                    f"{name} may only be defined by {symbol['path']}",
                    path,
                    exemptible=False,
                    details={"owner_path": symbol["path"], "symbol": name},
                )
            )
    return findings


def _active_migration_bridge_targets(
    policy: dict[str, Any],
    path: str,
    tree: ast.Module,
) -> set[str]:
    return {
        bridge["target_module"]
        for bridge in policy["migration_bridges"]
        if bridge["source_path"] == path and _bridge_import_count(tree, bridge) == 1
    }


def _legacy_api_cutover_findings(
    entry: dict[str, Any],
    *,
    path: str,
    old_path: str | None,
    base_source: str | None,
    head_source: str | None,
    git: _GitObjects,
    base: str,
    head: str,
) -> list[Finding]:
    details = {
        "source_path": entry["source_path"],
        "public_module": entry["public_module"],
        "canonical_module": entry["canonical_module"],
    }
    if old_path != path or base_source is None or head_source is None:
        return [
            Finding(
                "legacy_api_cutover_contract",
                "a legacy API cutover cannot delete or rename its declared source",
                path,
                exemptible=False,
                details=details,
            )
        ]
    base_tree = _parse_python(base_source, path, candidate=False)
    base_state = _legacy_api_cutover_state(base_tree, entry)
    if base_state == "consumed":
        findings = _legacy_api_cutover_target_findings(
            entry,
            git=git,
            revision=head,
            candidate=True,
        )
        if head_source != base_source:
            findings.append(
                Finding(
                    "legacy_api_cutover_retirement_required",
                    "a consumed legacy API cutover freezes its source until the authority entry is retired",
                    path,
                    exemptible=False,
                    details=details,
                )
            )
        return findings
    if base_state != "pending":
        return [
            Finding(
                "legacy_api_cutover_contract",
                "the base legacy API cutover state is neither pending nor consumed",
                path,
                exemptible=False,
                details=details,
            )
        ]
    if head_source == base_source:
        base_target_paths = {
            _module_python_path(entry["public_module"]),
            _module_python_path(entry["canonical_module"]),
        }
        if any(
            git.text(base, target_path, required=False)
            != git.text(head, target_path, required=False)
            for target_path in base_target_paths
        ):
            return [
                Finding(
                    "legacy_api_cutover_target_contract",
                    "pending cutover targets cannot change independently of the governed source",
                    target_path,
                    exemptible=False,
                    details=details,
                )
                for target_path in sorted(base_target_paths)
                if git.text(base, target_path, required=False)
                != git.text(head, target_path, required=False)
            ]
        return []

    head_tree = _parse_python(head_source, path, candidate=True)
    if not _legacy_api_cutover_attempted(base_tree, head_tree, entry):
        return []
    old_symbols = {rewrite["old_symbol"] for rewrite in entry["rewrites"]}
    head_bindings = _top_level_local_binding_counts(head_tree)
    contract_issues: list[str] = []
    if _legacy_api_cutover_import_count(head_tree, entry) != 1:
        contract_issues.append("public module import must appear exactly once")
    retained = sorted(symbol for symbol in old_symbols if head_bindings[symbol])
    if retained:
        contract_issues.append(f"legacy definitions remain: {', '.join(retained)}")
    if _removed_import_identities(head_tree, entry):
        contract_issues.append("declared obsolete imports remain")
    contract_issues.extend(_legacy_api_cutover_reference_issues(head_tree, entry))
    if not _dynamic_import_fingerprints(head_tree) <= _dynamic_import_fingerprints(base_tree):
        contract_issues.append("dynamic import capability was added")

    findings: list[Finding] = []
    if contract_issues:
        findings.append(
            Finding(
                "legacy_api_cutover_contract",
                "legacy API cutovers require one exact static import and complete declared rewrites",
                path,
                exemptible=False,
                details={**details, "issues": sorted(set(contract_issues))},
            )
        )

    findings.extend(
        _legacy_api_cutover_target_findings(
            entry,
            git=git,
            revision=head,
            candidate=True,
        )
    )

    base_nodes = _legacy_api_cutover_canonical_nodes(base_tree, entry, baseline=True)
    head_nodes = _legacy_api_cutover_canonical_nodes(head_tree, entry, baseline=False)
    base_lines = len(base_source.splitlines())
    head_lines = len(head_source.splitlines())
    if head_nodes != base_nodes or head_lines >= base_lines:
        findings.append(
            Finding(
                "legacy_api_cutover_source_logic",
                "legacy API cutovers may only remove declared definitions/imports and rewrite declared symbol uses",
                path,
                exemptible=False,
                details={
                    **details,
                    "base_lines": base_lines,
                    "head_lines": head_lines,
                    "canonical_ast_equal": head_nodes == base_nodes,
                },
            )
        )
    return findings


class _LegacyApiCutoverNormalizer(ast.NodeTransformer):
    def __init__(self, entry: dict[str, Any]) -> None:
        self._alias = entry["module_alias"]
        self._new_to_old = {
            rewrite["new_symbol"]: rewrite["old_symbol"]
            for rewrite in entry["rewrites"]
        }

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == self._alias
            and node.attr in self._new_to_old
        ):
            return ast.copy_location(
                ast.Name(id=self._new_to_old[node.attr], ctx=node.ctx),
                node,
            )
        return node


def _top_level_node_binding_names(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return set().union(*(_assignment_target_names(target) for target in node.targets))
    if isinstance(node, ast.AnnAssign):
        return _assignment_target_names(node.target)
    return set()


def _legacy_api_cutover_canonical_nodes(
    tree: ast.Module,
    entry: dict[str, Any],
    *,
    baseline: bool,
) -> tuple[str, ...]:
    old_symbols = {rewrite["old_symbol"] for rewrite in entry["rewrites"]}
    normalizer = _LegacyApiCutoverNormalizer(entry)
    nodes: list[str] = []
    for node in tree.body:
        if baseline:
            bindings = _top_level_node_binding_names(node)
            if len(bindings) == 1 and bindings <= old_symbols:
                continue
            if _is_declared_removed_import(node, entry):
                continue
            candidate = node
        else:
            if _is_legacy_api_cutover_import(node, entry):
                continue
            candidate = normalizer.visit(copy.deepcopy(node))
            ast.fix_missing_locations(candidate)
        nodes.append(ast.dump(candidate, include_attributes=False))
    return tuple(nodes)


def _bridge_import_count(tree: ast.Module, bridge: dict[str, Any]) -> int:
    return sum(
        1
        for node in tree.body
        if _is_bridge_import(node, bridge)
    )


def _is_bridge_import(node: ast.stmt, bridge: dict[str, Any]) -> bool:
    return (
        isinstance(node, ast.Import)
        and len(node.names) == 1
        and node.names[0].name == bridge["target_module"]
        and node.names[0].asname == bridge["module_alias"]
    )


def _bridge_alias_assignment_name(
    node: ast.stmt,
    bridge: dict[str, Any],
) -> str | None:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = set().union(*(_assignment_target_names(target) for target in targets))
    if len(names) != 1:
        return None
    exact_name = next(iter(names))
    value = node.value
    if (
        exact_name in set(bridge["symbols"])
        and isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == bridge["module_alias"]
        and value.attr == exact_name
    ):
        return exact_name
    return None


def _active_migration_bridge_nodes(
    policy: dict[str, Any],
    path: str,
    tree: ast.Module,
) -> set[int]:
    allowed_nodes: set[int] = set()
    for bridge in policy["migration_bridges"]:
        if bridge["source_path"] != path or _bridge_import_count(tree, bridge) != 1:
            continue
        for node in tree.body:
            if _is_bridge_import(node, bridge):
                allowed_nodes.add(id(node))
                continue
            if _bridge_alias_assignment_name(node, bridge) is not None:
                allowed_nodes.add(id(node))
    return allowed_nodes


def _migration_bridge_findings(
    policy: dict[str, Any],
    *,
    path: str,
    old_path: str | None,
    base_source: str | None,
    head_source: str,
    head_tree: ast.Module,
    git: _GitObjects,
    head: str,
) -> list[Finding]:
    findings: list[Finding] = []
    base_tree = (
        _parse_python(base_source, old_path or path, candidate=False)
        if base_source is not None and old_path == path
        else None
    )
    cutover = next(
        (
            entry
            for entry in policy["legacy_api_cutovers"]
            if entry["source_path"] == path
        ),
        None,
    )
    cutover_attempted = (
        cutover is not None
        and base_tree is not None
        and _legacy_api_cutover_state(base_tree, cutover) == "pending"
        and _legacy_api_cutover_attempted(base_tree, head_tree, cutover)
    )
    active_bridge_nodes = _active_migration_bridge_nodes(policy, path, head_tree)
    if cutover_attempted:
        active_bridge_nodes.update(
            id(node)
            for node in head_tree.body
            if _is_legacy_api_cutover_import(node, cutover)
        )
    for bridge in policy["migration_bridges"]:
        if bridge["source_path"] != path:
            continue
        base_active = base_tree is not None and _bridge_import_count(base_tree, bridge) == 1
        base_definitions = _bridge_definition_fingerprints(base_tree, bridge["symbols"])
        head_definitions = _bridge_definition_fingerprints(head_tree, bridge["symbols"])
        declared_definition_drift = base_definitions != head_definitions
        dynamic_import_added = not _dynamic_import_fingerprints(head_tree) <= (
            _dynamic_import_fingerprints(base_tree)
        )
        dynamic_import_capabilities = sorted(
            set(_dynamic_import_capability_labels(head_tree))
            - set(_dynamic_import_capability_labels(base_tree))
        )
        if dynamic_import_added and not dynamic_import_capabilities:
            dynamic_import_capabilities = sorted(
                set(_dynamic_import_capability_labels(head_tree))
            )
        target_mentioned = any(
            isinstance(node, ast.Import)
            and any(alias.name == bridge["target_module"] for alias in node.names)
            for node in head_tree.body
        ) or any(
            isinstance(node, ast.Constant) and node.value == bridge["target_module"]
            for node in ast.walk(head_tree)
        )
        bridge_alias_mentioned = any(
            bridge["module_alias"] in _assignment_target_names(target)
            for node in head_tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
        head_active = _bridge_import_count(head_tree, bridge) == 1
        if not (
            base_active
            or target_mentioned
            or bridge_alias_mentioned
            or head_active
            or declared_definition_drift
            or dynamic_import_added
        ):
            continue
        details = {
            "source_path": bridge["source_path"],
            "target_module": bridge["target_module"],
        }
        if dynamic_import_added:
            details["dynamic_import_capabilities"] = dynamic_import_capabilities
        if old_path != path or not head_active:
            findings.append(
                Finding(
                    "migration_bridge_import_contract",
                    "migration bridges require one exact static module import and alias at the declared source path",
                    path,
                    exemptible=False,
                    details=details,
                )
            )
            continue

        target_path = f"{bridge['target_module'].replace('.', '/')}.py"
        target_source = git.text(head, target_path, required=False)
        if target_source is None:
            findings.append(
                Finding(
                    "migration_bridge_target_contract",
                    "migration bridge target module is missing from the candidate",
                    target_path,
                    exemptible=False,
                    details=details,
                )
            )
        else:
            target_tree = _parse_python(target_source, target_path, candidate=True)
            defined = {
                node.name
                for node in target_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            for node in target_tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        defined.update(_assignment_target_names(target))
                elif isinstance(node, ast.AnnAssign):
                    defined.update(_assignment_target_names(node.target))
            missing = sorted(set(bridge["symbols"]) - defined)
            if missing:
                findings.append(
                    Finding(
                        "migration_bridge_target_contract",
                        "migration bridge target must define every declared symbol",
                        target_path,
                        exemptible=False,
                        details={**details, "missing_symbols": missing},
                    )
                )

        exact_aliases: Counter[str] = Counter()
        rebound_names: set[str] = set()
        for node in head_tree.body:
            if _is_bridge_import(node, bridge):
                continue
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = set().union(*(_assignment_target_names(target) for target in targets))
            exact_name = _bridge_alias_assignment_name(node, bridge)
            if exact_name is not None:
                exact_aliases[exact_name] += 1
            elif names & (set(bridge["symbols"]) | {bridge["module_alias"]}):
                rebound_names.update(names)
        local_definitions = {
            node.name
            for node in head_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in set(bridge["symbols"])
        }
        missing_aliases = sorted(
            symbol for symbol in bridge["symbols"] if exact_aliases[symbol] == 0
        )
        duplicate_aliases = sorted(
            symbol for symbol in bridge["symbols"] if exact_aliases[symbol] > 1
        )
        if missing_aliases or duplicate_aliases or local_definitions or rebound_names:
            findings.append(
                Finding(
                    "migration_bridge_symbol_contract",
                    "every bridged symbol must be one exact identity alias with no local definition or rebinding",
                    path,
                    exemptible=False,
                    details={
                        **details,
                        "duplicate_aliases": duplicate_aliases,
                        "local_definitions": sorted(local_definitions),
                        "missing_aliases": missing_aliases,
                        "rebound_names": sorted(rebound_names),
                    },
                )
            )

        base_lines = len(base_source.splitlines()) if base_source is not None else 0
        head_lines = len(head_source.splitlines())
        violates_size = head_lines > base_lines if base_active else head_lines >= base_lines
        if violates_size:
            findings.append(
                Finding(
                    "migration_bridge_source_growth",
                    "migration bridge sources must shrink on activation and cannot grow afterward",
                    path,
                    exemptible=False,
                    details={**details, "base_lines": base_lines, "head_lines": head_lines},
                )
            )

        base_nodes = (
            Counter(_legacy_api_cutover_canonical_nodes(base_tree, cutover, baseline=True))
            if cutover_attempted and base_tree is not None and cutover is not None
            else Counter(
                ast.dump(node, include_attributes=False)
                for node in (base_tree.body if base_tree is not None else [])
            )
        )
        unexpected: list[ast.stmt] = []
        for node in head_tree.body:
            if cutover_attempted and cutover is not None:
                if _is_legacy_api_cutover_import(node, cutover):
                    fingerprint = ast.dump(node, include_attributes=False)
                else:
                    normalized = _LegacyApiCutoverNormalizer(cutover).visit(copy.deepcopy(node))
                    ast.fix_missing_locations(normalized)
                    fingerprint = ast.dump(normalized, include_attributes=False)
            else:
                fingerprint = ast.dump(node, include_attributes=False)
            if base_nodes[fingerprint] > 0:
                base_nodes[fingerprint] -= 1
            elif id(node) not in active_bridge_nodes:
                unexpected.append(node)
        if unexpected:
            findings.append(
                Finding(
                    "migration_bridge_source_logic",
                    "migration bridge sources may only remove legacy definitions and retain the declared import and identity aliases",
                    path,
                    min((getattr(node, "lineno", 0) for node in unexpected), default=0),
                    exemptible=False,
                    details={**details, "unexpected_nodes": len(unexpected)},
                )
            )
    return findings


def _bridge_definition_fingerprints(
    tree: ast.Module | None,
    symbols: Sequence[str],
) -> dict[str, str]:
    if tree is None:
        return {}
    declared = set(symbols)
    return {
        node.name: ast.dump(node, include_attributes=False)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in declared
    }


def _dynamic_import_fingerprints(tree: ast.Module | None) -> Counter[str]:
    if tree is None:
        return Counter()
    return Counter(
        ast.dump(node, include_attributes=False)
        for node in ast.walk(tree)
        if _is_dynamic_import_capability(node)
    )


def _dynamic_import_capability_labels(tree: ast.Module | None) -> set[str]:
    if tree is None:
        return set()
    return {
        label
        for node in ast.walk(tree)
        for label in _dynamic_import_capability_labels_for_node(node)
    }


def _dynamic_import_capability_labels_for_node(node: ast.AST) -> set[str]:
    labels: set[str] = set()
    if isinstance(node, ast.Import):
        labels.update(
            module
            for module in ("builtins", "importlib")
            if any(
                alias.name == module or alias.name.startswith(f"{module}.")
                for alias in node.names
            )
        )
    elif isinstance(node, ast.ImportFrom):
        labels.update(
            module
            for module in ("builtins", "importlib")
            if node.module == module
            or (node.module is not None and node.module.startswith(f"{module}."))
        )
        labels.update(
            alias.name
            for alias in node.names
            if alias.name in {"__import__", "import_module"}
        )
    elif isinstance(node, ast.Name) and node.id in {"__import__", "import_module"}:
        labels.add(node.id)
    elif isinstance(node, ast.Attribute) and node.attr in {"__import__", "import_module"}:
        labels.add(node.attr)
    elif isinstance(node, ast.Call):
        function_name = _call_name(node.func)
        if function_name in {
            "__import__",
            "eval",
            "exec",
            "getattr",
            "globals",
            "import_module",
            "locals",
            "vars",
        }:
            labels.add(function_name)
    return labels


def _is_dynamic_import_capability(node: ast.AST) -> bool:
    return bool(_dynamic_import_capability_labels_for_node(node))


def _facade_findings(facade: dict[str, Any], source: str, path: str) -> list[Finding]:
    tree = _parse_python(source, path, candidate=True)
    findings: list[Finding] = []
    canonical_bindings: set[str] = set()
    declared_exports: list[str] | None = None
    line_count = len(source.splitlines())
    if line_count > facade["max_lines"]:
        findings.append(
            Finding(
                "facade_size",
                "compatibility facade exceeds its bounded size",
                path,
                details={"actual": line_count, "maximum": facade["max_lines"]},
            )
        )
    allowed_top_level = (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)
    for index, node in enumerate(tree.body):
        if not isinstance(node, allowed_top_level):
            findings.append(
                Finding(
                    "facade_local_logic",
                    "imports-only compatibility facades cannot define executable logic",
                    path,
                    getattr(node, "lineno", 0),
                )
            )
        if isinstance(node, ast.Expr) and not (
            index == 0 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        ):
            findings.append(
                Finding(
                    "facade_local_logic",
                    "imports-only compatibility facades allow only one module docstring expression",
                    path,
                    getattr(node, "lineno", 0),
                )
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            is_dunder_all = all(
                isinstance(target, ast.Name) and target.id == "__all__" for target in targets
            )
            if not is_dunder_all:
                findings.append(
                    Finding(
                        "facade_local_state",
                        "imports-only compatibility facades may assign only __all__",
                        path,
                        getattr(node, "lineno", 0),
                    )
                )
                continue
            value = node.value
            if not isinstance(value, (ast.List, ast.Tuple)) or any(
                not isinstance(element, ast.Constant) or not isinstance(element.value, str)
                for element in value.elts
            ):
                findings.append(
                    Finding(
                        "facade_local_state",
                        "compatibility facade __all__ must be a static string list or tuple",
                        path,
                        getattr(node, "lineno", 0),
                    )
                )
            else:
                exports = [element.value for element in value.elts]
                if declared_exports is not None or len(exports) != len(set(exports)):
                    findings.append(
                        Finding(
                            "facade_export_contract",
                            "compatibility facade must define one duplicate-free static __all__",
                            path,
                            getattr(node, "lineno", 0),
                        )
                    )
                declared_exports = exports
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.With, ast.AsyncWith)):
            findings.append(
                Finding(
                    "facade_control_flow",
                    "compatibility facades cannot contain business control flow",
                    path,
                    node.lineno,
                )
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_TEXT.search(node.value):
            findings.append(
                Finding(
                    "facade_sql",
                    "compatibility facades cannot contain SQL",
                    path,
                    getattr(node, "lineno", 0),
                )
            )
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _module_has_prefix(module, facade["canonical_prefix"]):
                findings.append(
                    Finding(
                        "facade_import_forbidden",
                        "compatibility facade imports must come from the canonical owner",
                        path,
                        node.lineno,
                        details={"module": module},
                    )
                )
            else:
                if any(alias.name == "*" for alias in node.names):
                    findings.append(
                        Finding(
                            "facade_wildcard_import",
                            "compatibility facades cannot use wildcard imports",
                            path,
                            node.lineno,
                            exemptible=False,
                        )
                    )
                else:
                    canonical_bindings.update(alias.asname or alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_has_prefix(alias.name, facade["canonical_prefix"]):
                    findings.append(
                        Finding(
                            "facade_import_forbidden",
                            "compatibility facade imports must come from the canonical owner",
                            path,
                            node.lineno,
                            details={"module": alias.name},
                        )
                    )
                elif alias.asname is not None:
                    canonical_bindings.add(alias.asname)
        if isinstance(node, ast.Call):
            findings.append(
                Finding(
                    "facade_runtime_logic",
                    "compatibility facades cannot call providers, queues, storage, or business operations",
                    path,
                    node.lineno,
                )
            )
    if declared_exports is None or set(declared_exports) != canonical_bindings:
        findings.append(
            Finding(
                "facade_export_contract",
                "compatibility facade __all__ must exactly name its canonical imported bindings",
                path,
                details={
                    "declared_exports": [] if declared_exports is None else sorted(declared_exports),
                    "imported_bindings": sorted(canonical_bindings),
                },
            )
        )
    return _sort_findings(_deduplicate_findings(findings))


def _module_has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _registry_findings(registry: dict[str, Any], source: str, path: str) -> list[Finding]:
    tree = _parse_python(source, path, candidate=True)
    findings: list[Finding] = []
    literal_keys: list[tuple[str, int]] = []
    imported_constructors = _imported_symbol_bindings(tree)
    factories = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == registry["factory_function"]
    ]
    registry_dict: ast.Dict | None = None
    registry_factory: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    if len(factories) != 1:
        findings.append(
            Finding(
                "registry_factory_contract",
                f"production registry must define exactly one {registry['factory_function']} factory",
                path,
            )
        )
    else:
        registry_factory = factories[0]
        returns = [node for node in ast.walk(factories[0]) if isinstance(node, ast.Return)]
        if (
            len(factories[0].body) != 1
            or len(returns) != 1
            or not isinstance(returns[0].value, ast.Dict)
        ):
            findings.append(
                Finding(
                    "registry_factory_contract",
                    "production registry factory must return one literal dictionary",
                    path,
                    factories[0].lineno,
                )
            )
        else:
            registry_dict = returns[0].value

    if registry_dict is not None:
        current: set[str] = set()
        allowed_constructors = {
            (entry["module"], entry["name"])
            for entry in registry["adapter_constructors"]
        }
        for key, value in zip(registry_dict.keys, registry_dict.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                findings.append(
                    Finding(
                        "registry_nonliteral_key",
                        "production registry keys must be string literals",
                        path,
                        getattr(key, "lineno", registry_dict.lineno),
                    )
                )
                continue
            literal_keys.append((key.value, getattr(key, "lineno", registry_dict.lineno)))
            if key.value in current:
                findings.append(
                    Finding(
                        "registry_duplicate_key",
                        f"duplicate production registry key: {key.value}",
                        path,
                        getattr(key, "lineno", registry_dict.lineno),
                    )
                )
            current.add(key.value)
            constructor = _call_name(value.func) if isinstance(value, ast.Call) else ""
            constructor_identity = (
                imported_constructors.get(constructor)
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                else None
            )
            if (
                not isinstance(value, ast.Call)
                or constructor_identity not in allowed_constructors
                or value.args
                or value.keywords
            ):
                findings.append(
                    Finding(
                        "registry_adapter_mismatch",
                        "production registry values must be zero-argument declared adapter constructors",
                        path,
                        getattr(value, "lineno", registry_dict.lineno),
                        exemptible=False,
                        details={
                            "actual_constructor": constructor or None,
                            "actual_identity": None
                            if constructor_identity is None
                            else ".".join(constructor_identity),
                            "allowed_constructors": [
                                ".".join(identity) for identity in sorted(allowed_constructors)
                            ],
                            "key": key.value,
                        },
                    )
                )
            for value_node in ast.walk(value):
                if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                    _append_test_double_findings(
                        findings,
                        registry,
                        [value_node.value],
                        path,
                        getattr(value_node, "lineno", registry_dict.lineno),
                    )
                elif isinstance(value_node, (ast.Name, ast.Attribute)):
                    value_name = value_node.id if isinstance(value_node, ast.Name) else value_node.attr
                    _append_test_double_findings(
                        findings,
                        registry,
                        [value_name],
                        path,
                        getattr(value_node, "lineno", registry_dict.lineno),
                    )

    for node in ast.walk(registry_factory) if registry_factory is not None else ():
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            if any(
                name in {"importlib", "os", "shlex", "subprocess"}
                or name.startswith(("importlib.", "os.", "subprocess."))
                for name in names
            ):
                findings.append(
                    Finding(
                        "registry_dynamic_selector",
                        "production registries cannot dynamically import configured targets",
                        path,
                        node.lineno,
                        exemptible=False,
                    )
                )
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            _append_test_double_findings(findings, registry, [name], path, getattr(node, "lineno", 0))
        if isinstance(node, ast.Call):
            function_name = _call_name(node.func)
            if function_name in {
                "Popen",
                "__import__",
                "call",
                "check_call",
                "check_output",
                "eval",
                "exec",
                "getattr",
                "import_module",
                "popen",
                "run",
                "system",
            } or function_name.startswith(("execl", "execv", "spawn")):
                findings.append(
                    Finding(
                        "registry_dynamic_selector",
                        "production registries cannot resolve arbitrary module, class, or command selectors",
                        path,
                        node.lineno,
                        exemptible=False,
                        details={"call": function_name},
                    )
                )

    allowed = set(registry["allowed_keys"])
    found = {key for key, _line in literal_keys}
    for key, line in literal_keys:
        if key not in allowed:
            findings.append(
                Finding(
                    "registry_unknown_key",
                    f"production registry key is not declared by authority policy: {key}",
                    path,
                    line,
                    details={"key": key},
                )
            )
        _append_test_double_findings(findings, registry, [key], path, line)
    for missing in sorted(allowed - found):
        findings.append(
            Finding(
                "registry_missing_key",
                f"declared production registry key is missing: {missing}",
                path,
                details={"key": missing},
            )
        )
    return _sort_findings(_deduplicate_findings(findings))


def _imported_symbol_bindings(tree: ast.Module) -> dict[str, tuple[str, str]]:
    bindings: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                bindings[alias.asname or alias.name] = (node.module, alias.name)
            continue
        for name in _module_scope_rebindings(node):
            bindings.pop(name, None)
    return bindings


def _module_scope_rebindings(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return {node.name}
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names: set[str] = set()
        for target in targets:
            names.update(_assignment_target_names(target))
        return names
    if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor)):
        names = _assignment_target_names(node.target)
    elif isinstance(node, ast.Delete):
        names = set()
        for target in node.targets:
            names.update(_assignment_target_names(target))
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        names = {
            alias.asname or alias.name.split(".", 1)[0]
            for alias in node.names
        }
    else:
        names = set()

    nested_bodies: list[list[ast.stmt]] = []
    for attribute in ("body", "orelse", "finalbody"):
        body = getattr(node, attribute, None)
        if isinstance(body, list):
            nested_bodies.append(body)
    if isinstance(node, ast.Try):
        nested_bodies.extend(handler.body for handler in node.handlers)
    if isinstance(node, ast.Match):
        nested_bodies.extend(case.body for case in node.cases)
    for body in nested_bodies:
        for statement in body:
            names.update(_module_scope_rebindings(statement))
    return names


def _registry_selector_findings(
    registry: dict[str, Any],
    git: _GitObjects,
    head: str,
) -> list[Finding]:
    findings: list[Finding] = []
    expected = set(registry["allowed_keys"])
    for selector in registry["selector_owners"]:
        path = selector["path"]
        symbol = selector["symbol"]
        source = git.text(head, path, required=False)
        actual = None if source is None else _literal_collection_assignment(
            _parse_python(source, path, candidate=True),
            symbol,
        )
        if actual != expected:
            findings.append(
                Finding(
                    "registry_selector_mismatch",
                    f"{symbol} must select exactly the authority registry keys",
                    path,
                    exemptible=False,
                    details={
                        "actual": None if actual is None else sorted(actual),
                        "expected": sorted(expected),
                        "symbol": symbol,
                    },
                )
            )
    return findings


def _literal_collection_assignment(tree: ast.Module, symbol: str) -> set[str] | None:
    matches: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == symbol for target in node.targets
        ):
            matches.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == symbol:
            if node.value is not None:
                matches.append(node.value)
    if len(matches) != 1 or not isinstance(matches[0], (ast.List, ast.Set, ast.Tuple)):
        return None
    values: set[str] = set()
    for element in matches[0].elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.add(element.value)
    return values


def _append_test_double_findings(
    findings: list[Finding],
    registry: dict[str, Any],
    values: Iterable[str],
    path: str,
    line: int,
) -> None:
    terms = set(term.lower() for term in registry["test_double_terms"])
    for value in values:
        tokens = {
            token.lower()
            for token in re.findall(
                r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+",
                value.replace("-", " ").replace("_", " ").replace(".", " "),
            )
        }
        matched = next((term for term in sorted(terms) if term in tokens), None)
        if matched is not None:
            findings.append(
                Finding(
                    "registry_test_double",
                    "test doubles cannot enter a production runtime registry",
                    path,
                    line,
                    exemptible=False,
                    details={"matched_term": matched, "value": value},
                )
            )


def _call_name(function: ast.expr) -> str:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _deduplicate_findings(findings: Iterable[Finding]) -> list[Finding]:
    unique: dict[tuple[str, str, int, str, str, bool], Finding] = {}
    for item in findings:
        details = json.dumps(item.details, ensure_ascii=False, sort_keys=True)
        unique[(item.code, item.path, item.line, item.message, details, item.exemptible)] = item
    return list(unique.values())


def _sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (
            item.path,
            item.line,
            item.code,
            item.message,
            json.dumps(item.details, ensure_ascii=False, sort_keys=True),
        ),
    )


def _is_python_path(path: str) -> bool:
    return PurePosixPath(path).suffix == ".py"


def _require_exact_keys(value: Any, expected: set[str], label: str, error_code: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ArchitectureError(error_code, f"{label} keys must be exactly: {', '.join(sorted(expected))}")


def _require_nonempty(value: dict[str, Any], key: str, error_code: str) -> None:
    if not isinstance(value.get(key), str) or not value[key].strip():
        raise ArchitectureError(error_code, f"{key} must be a non-empty string")


def _require_path(value: Any, label: str, error_code: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ArchitectureError(error_code, f"{label} must be a non-empty repository-relative POSIX path")
    if ".." in PurePosixPath(value).parts:
        raise ArchitectureError(error_code, f"{label} cannot traverse outside the repository")
    return value


def _unique_name_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ArchitectureError("invalid_policy", f"{label} must be a non-empty list")
    if not all(isinstance(item, str) and MODULE_NAME.fullmatch(item) is not None for item in value):
        raise ArchitectureError("invalid_policy", f"{label} must contain lower-snake-case names")
    if value != sorted(set(value)):
        raise ArchitectureError("invalid_policy", f"{label} must be sorted and unique")
    return value


def _unique_path_list(value: Any, label: str, *, error_code: str = "invalid_policy") -> list[str]:
    if not isinstance(value, list) or not value:
        raise ArchitectureError(error_code, f"{label} must be a non-empty list")
    paths = [_require_path(item, label, error_code) for item in value]
    if paths != sorted(set(paths)):
        raise ArchitectureError(error_code, f"{label} must be sorted and unique")
    return paths


def _parse_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ArchitectureError("invalid_exception", f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ArchitectureError("invalid_exception", f"{label} must be an ISO date") from exc


def _command_failure(label: str, result: _CommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    return f"{label} failed: {detail}"


def _render_text(evaluation: Evaluation) -> str:
    lines = [
        f"architecture-governance: {evaluation.status.upper()}",
        f"schema: {REPORT_SCHEMA_VERSION}",
        f"authority: {evaluation.authority_ref}",
        f"base: {evaluation.base_ref}",
        f"head: {evaluation.head_ref}",
        f"policy: {evaluation.policy['schema_version']} ({evaluation.policy['path']})",
        "findings:" if evaluation.findings else "findings: none",
    ]
    for finding in evaluation.findings:
        location = f"{finding.path}:{finding.line}" if finding.line else finding.path
        lines.append(f"- {finding.code} [{location}]: {finding.message}")
    if evaluation.exempted_findings:
        lines.append("exempted findings:")
        for finding in evaluation.exempted_findings:
            location = f"{finding.path}:{finding.line}" if finding.line else finding.path
            lines.append(f"- {finding.code} [{location}]: {finding.message}")
    return "\n".join(lines)


def _error_payload(error: ArchitectureError) -> dict[str, Any]:
    return {
        "error": {"code": error.code, "message": str(error)},
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "error",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArchitectureArgumentParser(description="Evaluate trusted ai-platform architecture gates.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check one exact authority/base/head range")
    check.add_argument("--authority-ref", required=True)
    check.add_argument("--base-ref", required=True)
    check.add_argument("--head-ref", required=True)
    check.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    requested_format = "json" if _argument_value(arguments, "--format") == "json" else "text"
    try:
        args = _build_parser().parse_args(arguments)
        evaluation = ArchitectureEvaluator(Path.cwd()).evaluate(
            args.authority_ref,
            args.base_ref,
            args.head_ref,
        )
    except ArchitectureError as error:
        if requested_format == "json":
            print(json.dumps(_error_payload(error), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"architecture-governance: ERROR\n{error.code}: {error}")
        return 3
    if args.format == "json":
        print(json.dumps(evaluation.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(evaluation))
    return evaluation.exit_code


def _argument_value(arguments: Sequence[str], name: str) -> str | None:
    prefix = f"{name}="
    for argument in arguments:
        if argument.startswith(prefix):
            return argument[len(prefix) :]
    try:
        index = arguments.index(name)
    except ValueError:
        return None
    return arguments[index + 1] if index + 1 < len(arguments) else None


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluate an exact Git range against the trusted backend architecture policy.

The executable, policy schema, and normal policy are authority objects. Candidate
filesystem contents cannot relax their own result. Legacy root allowances may
outlive deleted modules; they never authorize adding or restoring root code.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
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
SUPPORTED_SCHEMA_PATTERNS = frozenset(
    {
        r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.\.(?:/|$)).+$",
        r"^[A-Z][A-Z0-9_]+$",
        r"^[A-Z][A-Za-z0-9]*$",
        r"^_?[A-Za-z][A-Za-z0-9_]*$",
        r"^[a-z][a-z0-9_]*$",
        r"^_?[a-z][a-z0-9_]*$",
        r"^app(?:\.[a-z][a-z0-9_]*)+$",
        r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
        r"^[A-Za-z_][A-Za-z0-9_]*$",
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
    "production_registries",
    "governed_symbols",
    "exception_contract",
}
LAYER_NAMES = ("application", "domain", "infrastructure", "transport")
BUILTIN_NON_EXEMPTIBLE_CODES = frozenset({
    "compatibility_import_forbidden",
    "cross_domain_internal_import",
    "governed_symbol_missing",
    "governed_symbol_owner",
    "kernel_product_import",
    "kernel_public_surface_forbidden",
    "layer_external_dependency_forbidden",
    "layer_dependency_forbidden",
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
        self._blobs: dict[tuple[str, str], bytes] = {}

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
        key = (commit, path)
        if key in self._blobs:
            return self._blobs[key]
        result = self.runner.run_bytes(("git", "show", f"{commit}:{path}"), cwd=self.repo_root)
        if result.returncode == 0:
            self._blobs[key] = result.stdout
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
        _validate_policy(policy, self._git, authority)

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
            if is_new_location and len(parts) == 2 and (
                path not in approved_roots
                or self._git.blob(base, path, required=False) is None
            ):
                findings.append(
                    Finding(
                        "unapproved_app_root_module",
                        "new or restored app-root modules must move to an owning package",
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
            base_targets: set[str] = set()
            base_text: str | None = None
            relocated_edges: set[_ImportEdge] = set()
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
                        relocated_edges = _identity_import_contraction_edges(
                            path=path,
                            base_tree=base_tree,
                            head_tree=head_tree,
                            git=self._git,
                            base=base,
                            head=head,
                            base_modules=base_modules,
                            head_modules=head_modules,
                        )
            for edge in _import_edges(head_tree, path, known_modules=head_modules):
                if edge.target in base_targets:
                    continue
                if edge in relocated_edges:
                    continue
                finding = _new_edge_finding(policy, path, edge)
                if finding is not None:
                    findings.append(finding)

            findings.extend(_governed_symbol_findings(policy, path, old_path, head_tree, self._git, base))

        for hot_file in policy["frozen_hot_files"]:
            path = hot_file["path"]
            if path not in changed_by_head:
                continue
            old_text = self._git.text(base, changed_by_head[path].old_path or path, required=False)
            head_text = self._git.text(head, path)
            old_lines = 0 if old_text is None else len(old_text.splitlines())
            head_lines = len(head_text.splitlines())
            import_only_expansion = False
            if old_text is not None and head_lines > old_lines:
                old_tree = _parse_python(old_text, path, candidate=False)
                new_tree = _parse_python(head_text, path, candidate=True)
                contractions = _identity_import_contraction_edges(
                    path=path, base_tree=old_tree, head_tree=new_tree,
                    git=self._git, base=base, head=head,
                    base_modules=base_modules, head_modules=head_modules,
                )
                import_only_expansion = bool(contractions) and (
                    _identity_normalized_body(
                        old_tree, git=self._git, revision=base,
                        known_modules=base_modules, candidate=False,
                    ) == _identity_normalized_body(
                        new_tree, git=self._git, revision=head,
                        known_modules=head_modules, candidate=True,
                    )
                )
            if (head_lines > old_lines and not import_only_expansion) or head_lines > hot_file["max_lines"]:
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
        base_raw = self._git.text(base, exception_path, required=False)
        if raw == base_raw:
            return list(findings), [], {
                "path": exception_path,
                "status": "inherited_inactive",
            }
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
        "governedSymbol",
        "hotFile",
        "layerPolicy",
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
        if expected_type == ["string", "null"]:
            if value is None:
                return
            expected_type = "string"
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
    if not set(authority_root_modules).issubset(approved):
        raise ArchitectureError(
            "invalid_policy",
            "approved_root_modules must include every existing authority app-root Python module",
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


def _module_python_path(module: str) -> str:
    return f"{module.replace('.', '/')}.py"


def _top_level_local_binding_counts(tree: ast.Module) -> Counter[str]:
    counts: Counter[str] = Counter()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name] += 1
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                counts.update(_assignment_target_names(target))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            counts.update(_assignment_target_names(node.target))
    return counts


def _python_modules(paths: Sequence[str]) -> set[str]:
    return {
        _path_module(path)
        for path in paths
        if _is_python_path(path)
    }


def _enclosing_import_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    *,
    include_self: bool = True,
) -> ast.AST | None:
    scope_types = (
        ast.Module,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Lambda,
        ast.ClassDef,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    current = node if include_self else parents.get(node)
    while current is not None:
        if isinstance(current, scope_types):
            return current
        current = parents.get(current)
    return None


def _dynamic_import_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.AST | None:
    original = node
    scope = _enclosing_import_scope(node, parents, include_self=False)
    while scope is not None:
        child = node
        while parents.get(child) is not scope:
            child = parents[child]
        if isinstance(scope, ast.Module):
            return scope
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if child in scope.body:
                return scope
        elif isinstance(scope, ast.Lambda):
            if child is scope.body:
                return scope
        else:
            first_iter = scope.generators[0].iter
            current: ast.AST | None = original
            while current is not None and current is not first_iter:
                current = parents.get(current)
            if current is None:
                return scope
        node = scope
        scope = _enclosing_import_scope(scope, parents, include_self=False)
    return None


def _literal_call_argument(call: ast.Call, position: int, name: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _literal_dynamic_import_edges(tree: ast.Module) -> tuple[_ImportEdge, ...]:
    nodes = tuple(ast.walk(tree))
    parents = {
        child: node
        for node in nodes
        for child in ast.iter_child_nodes(node)
    }
    bindings: dict[
        ast.AST, dict[str, list[tuple[int, int, str, ast.AST | None]]]
    ] = {}
    delegated: dict[ast.AST, dict[str, str]] = {}
    comprehension_scopes = (
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    function_scopes = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Lambda,
        *comprehension_scopes,
    )

    def bind(
        scope: ast.AST | None,
        name: str | None,
        identity: str,
        node: ast.AST,
        *,
        after: ast.AST | None = None,
        origin: ast.AST | None = None,
    ) -> None:
        if scope is not None and name:
            position = after or node
            line_attribute = "end_lineno" if after is not None else "lineno"
            column_attribute = "end_col_offset" if after is not None else "col_offset"
            bindings.setdefault(scope, {}).setdefault(name, []).append(
                (
                    getattr(position, line_attribute, 0),
                    getattr(position, column_attribute, 0),
                    identity,
                    origin,
                )
            )

    for node in nodes:
        scope = _enclosing_import_scope(node, parents)
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                bind(
                    scope,
                    name,
                    "importlib"
                    if alias.name == "importlib"
                    or (alias.asname is None and alias.name.startswith("importlib."))
                    else "other",
                    alias,
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                identity = (
                    "import_module"
                    if node.level == 0
                    and node.module == "importlib"
                    and alias.name == "import_module"
                    else "other"
                )
                bind(scope, alias.asname or alias.name, identity, alias)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            parent = parents.get(node)
            if (
                isinstance(parent, ast.AnnAssign)
                and parent.value is None
                and not isinstance(scope, function_scopes)
            ):
                continue
            binding_scope = scope
            assignment = parent
            while isinstance(assignment, (ast.List, ast.Starred, ast.Tuple)):
                assignment = parents.get(assignment)
            if isinstance(parent, ast.NamedExpr):
                binding_scope = _dynamic_import_scope(node, parents)
                while isinstance(binding_scope, comprehension_scopes):
                    binding_scope = _dynamic_import_scope(binding_scope, parents)
            after = None
            if isinstance(assignment, (ast.AnnAssign, ast.Assign, ast.AugAssign, ast.NamedExpr)):
                after = assignment.value
            elif isinstance(assignment, (ast.AsyncFor, ast.For)):
                after = assignment.iter
            bind(
                binding_scope,
                node.id,
                "other",
                node,
                after=after,
                origin=assignment if after is not None else None,
            )
        elif isinstance(node, ast.arg):
            bind(scope, node.arg, "other", node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(
                _enclosing_import_scope(node, parents, include_self=False),
                node.name,
                "other",
                node,
                origin=node,
            )
        elif isinstance(node, ast.ExceptHandler):
            bind(scope, node.name, "other", node)
        elif isinstance(node, ast.Global) and scope is not None:
            delegated.setdefault(scope, {}).update(
                (name, "global") for name in node.names
            )
        elif isinstance(node, ast.Nonlocal) and scope is not None:
            delegated.setdefault(scope, {}).update(
                (name, "nonlocal") for name in node.names
            )
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            bind(scope, node.name, "other", node)
        elif isinstance(node, ast.MatchMapping):
            bind(scope, node.rest, "other", node)

    def direct_child(node: ast.AST, ancestor: ast.AST) -> ast.AST | None:
        current = node
        while parents.get(current) is not ancestor:
            current = parents.get(current)
            if current is None:
                return None
        return current

    def is_within(node: ast.AST, ancestor: ast.AST) -> bool:
        current: ast.AST | None = node
        while current is not None:
            if current is ancestor:
                return True
            current = parents.get(current)
        return False

    def definition_is_bound(definition: ast.AST, call: ast.Call) -> bool:
        child = direct_child(call, definition)
        if child is None:
            return True
        if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return child in definition.body
        if child not in definition.body:
            return False
        current: ast.AST | None = call
        while current is not None and current is not definition:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_child = direct_child(call, current)
                if function_child in current.body:
                    return True
            elif isinstance(current, ast.Lambda):
                parent = parents.get(current)
                lambda_child = direct_child(call, current)
                if lambda_child is current.body and not (
                    isinstance(parent, ast.Call) and parent.func is current
                ):
                    return True
            elif isinstance(current, ast.GeneratorExp) and not is_within(
                call, current.generators[0].iter
            ):
                return True
            current = parents.get(current)
        return False

    def event_is_visible(
        event: tuple[int, int, str, ast.AST | None],
        call: ast.Call,
        *,
        limit_position: bool,
    ) -> bool:
        line, column, _identity, origin = event
        if isinstance(origin, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not definition_is_bound(origin, call):
                return False
        if (
            isinstance(origin, ast.AnnAssign)
            and origin.value is not None
            and is_within(call, origin.annotation)
        ):
            return True
        return not limit_position or (line, column) <= (call.lineno, call.col_offset)

    def latest_identity(
        events: list[tuple[int, int, str, ast.AST | None]],
    ) -> str | None:
        position = max(event[:2] for event in events)
        identities = {
            identity
            for line, column, identity, _origin in events
            if (line, column) == position
        }
        return next(iter(identities)) if len(identities) == 1 else None

    def resolve(call: ast.Call, name: str) -> str | None:
        scope = _dynamic_import_scope(call, parents)
        current_scope = scope
        inside_function = False
        while scope is not None:
            if not (inside_function and isinstance(scope, ast.ClassDef)):
                events = bindings.get(scope, {}).get(name, [])
                declaration = delegated.get(scope, {}).get(name)
                visible = [
                    event
                    for event in events
                    if event_is_visible(
                        event,
                        call,
                        limit_position=scope is current_scope or declaration is not None,
                    )
                ]
                if declaration is not None:
                    if visible:
                        return latest_identity(visible)
                    if isinstance(scope, function_scopes):
                        inside_function = True
                    if declaration == "global":
                        scope = tree
                    else:
                        scope = _dynamic_import_scope(scope, parents)
                    continue
                if events:
                    if visible:
                        if scope is current_scope or isinstance(scope, ast.Module):
                            return latest_identity(visible)
                        identities = {
                            identity for _line, _column, identity, _origin in visible
                        }
                        return (
                            next(iter(identities)) if len(identities) == 1 else None
                        )
                    if isinstance(scope, function_scopes):
                        return None
            if isinstance(scope, function_scopes):
                inside_function = True
            scope = _dynamic_import_scope(scope, parents)
        return "__import__" if name == "__import__" else None

    edges: list[_ImportEdge] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        loader: str | None = None
        if isinstance(node.func, ast.Name):
            loader = resolve(node, node.func.id)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and resolve(node, node.func.value.id) == "importlib"
        ):
            loader = "import_module"
        if loader not in {"__import__", "import_module"}:
            continue
        target_argument = _literal_call_argument(node, 0, "name")
        if not (
            isinstance(target_argument, ast.Constant)
            and isinstance(target_argument.value, str)
        ):
            continue
        target = target_argument.value
        if loader == "__import__":
            level_argument = _literal_call_argument(node, 4, "level")
            if level_argument is not None and not (
                isinstance(level_argument, ast.Constant)
                and isinstance(level_argument.value, int)
                and level_argument.value == 0
            ):
                continue
            if target.startswith("."):
                continue
        elif target.startswith("."):
            package_argument = _literal_call_argument(node, 1, "package")
            if not (
                isinstance(package_argument, ast.Constant)
                and isinstance(package_argument.value, str)
            ):
                continue
            try:
                target = importlib.util.resolve_name(target, package_argument.value)
            except ImportError:
                continue
        edges.append(_ImportEdge(target, node.lineno))
    return tuple(edges)


def _import_edges(
    tree: ast.Module,
    path: str,
    *,
    known_modules: set[str] | None = None,
) -> tuple[_ImportEdge, ...]:
    package_parts = list(PurePosixPath(path).with_suffix("").parts[:-1])
    edges: list[_ImportEdge] = list(_literal_dynamic_import_edges(tree))
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
    if (
        source.package == "platform"
        and target.package not in {None, "kernel", "platform"}
    ):
        return Finding(
            "platform_product_import",
            "platform technical clients cannot import product or composition packages",
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
            if (
                source.layer is not None
                and target.layer is None
                and target.boundary is None
            ):
                return Finding(
                    "layer_dependency_forbidden",
                    "canonical layers cannot import unlayered same-context legacy modules",
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
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
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


def _static_import_bindings(
    tree: ast.Module, known_modules: set[str]
) -> dict[str, tuple[str, str | None]]:
    """Resolve explicit imports and their existing identity aliases, without evaluation."""
    bindings: dict[str, tuple[str, str | None]] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname or "." not in alias.name:
                    bindings[alias.asname or alias.name] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                if alias.name == "*":
                    continue
                module = node.module or ""
                candidate = f"{module}.{alias.name}"
                bindings[alias.asname or alias.name] = (
                    (candidate, None) if candidate in known_modules
                    else (module, alias.name)
                )
        else:
            alias_binding = None
            if (
                isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
            ):
                origin = bindings.get(node.value.value.id)
                if origin is not None and origin[1] is None:
                    alias_binding = (node.targets[0].id, (origin[0], node.value.attr))
            names = _top_level_node_binding_names(node)
            if names:
                for name in names:
                    bindings.pop(name, None)
            elif not (
                isinstance(node, ast.Pass)
                or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            ):
                # Control flow, deletion, and executable expressions are not
                # evidence of an unconditional static binding.
                bindings.clear()
            if alias_binding is not None:
                bindings[alias_binding[0]] = alias_binding[1]
    return bindings


def _top_level_import_binding_counts(tree: ast.Module) -> Counter[str]:
    counts: Counter[str] = Counter()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    counts[alias.asname] += 1
                elif "." not in alias.name:
                    counts[alias.name] += 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    counts[alias.asname or alias.name] += 1
    return counts


def _identity_import_bindings(
    tree: ast.Module, known_modules: set[str]
) -> list[tuple[str, tuple[str, str | None], ast.alias]]:
    imports: list[tuple[str, tuple[str, str | None], ast.alias]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                # An unaliased dotted import binds its first component in Python.
                # Keep this proof limited to unambiguous local binding names.
                if not alias.asname and "." in alias.name:
                    continue
                local_name = alias.asname or alias.name
                single = ast.Module(
                    body=[ast.Import(names=[alias])], type_ignores=[]
                )
                origin = _static_import_bindings(single, known_modules).get(local_name)
                if origin is not None:
                    imports.append((local_name, origin, alias))
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                single = ast.Module(
                    body=[ast.ImportFrom(module=node.module, names=[alias], level=0)],
                    type_ignores=[],
                )
                origin = _static_import_bindings(single, known_modules).get(local_name)
                if origin is not None:
                    imports.append((local_name, origin, alias))
    return imports


def _identity_import_name_is_shadowed(
    tree: ast.Module, name: str, selected_alias: ast.alias
) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.alias) and node is not selected_alias:
            imported_name = node.asname or node.name.split(".", 1)[0]
            if imported_name == name:
                return True
        elif isinstance(node, ast.arg) and node.arg == name:
            return True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name and node is not tree:
                return True
        elif isinstance(node, ast.Name) and node.id == name and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            return True
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            return True
        elif isinstance(node, ast.MatchMapping) and node.rest == name:
            return True
    return False


def _identity_import_uses(
    tree: ast.Module,
    name: str,
    origin: tuple[str, str | None],
    *,
    git: _GitObjects,
    revision: str,
    known_modules: set[str],
    candidate: bool,
) -> set[tuple[str, str]] | None:
    """Return the canonical symbols reached by one unambiguous imported name."""
    selected_alias = _identity_selected_alias(tree, name)
    if selected_alias is None or _identity_import_name_is_shadowed(tree, name, selected_alias):
        return None
    parents = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    symbols: set[tuple[str, str]] = set()
    found = False
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, ast.Load)
        ):
            continue
        parent = parents.get(node)
        if origin[1] is None:
            if not (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and isinstance(parent.ctx, ast.Load)
            ):
                return None
            grandparent = parents.get(parent)
            if isinstance(grandparent, ast.Attribute) and grandparent.value is parent:
                return None
            symbol = parent.attr
            canonical = _identity_import_module_symbol_origin(
                git, revision, origin[0], symbol, known_modules,
                candidate=candidate,
            )
        else:
            if (
                isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name)
                and parent.func.id == "getattr"
                and node in parent.args
            ):
                return None
            symbol = origin[1]
            canonical = _identity_import_module_symbol_origin(
                git, revision, origin[0], symbol, known_modules,
                candidate=candidate,
            )
        if canonical is None or (candidate and canonical != (origin[0], symbol)):
            return None
        symbols.add(canonical)
        found = True
    return symbols if found else None


def _identity_selected_alias(tree: ast.Module, name: str) -> ast.alias | None:
    aliases = [
        alias
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.name != "*" and (alias.asname or alias.name) == name
    ]
    return aliases[0] if len(aliases) == 1 else None


def _identity_import_module_symbol_origin(
    git: _GitObjects, revision: str, module: str, symbol: str,
    known_modules: set[str], *, candidate: bool,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[str, str] | None:
    if (module, symbol) in seen:
        return None
    cache = getattr(git, "_identity_origins", None)
    if cache is None:
        cache = {}
        git._identity_origins = cache
    key = (revision, module, symbol, candidate)
    if key not in cache:
        cache[key] = _resolve_identity_import_symbol(
            git, revision, module, symbol, known_modules,
            candidate=candidate, seen=seen,
        )
    return cache[key]


def _resolve_identity_import_symbol(
    git: _GitObjects,
    revision: str,
    module: str,
    symbol: str,
    known_modules: set[str],
    *,
    candidate: bool,
    seen: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[str, str] | None:
    key = (module, symbol)
    if key in seen:
        return None
    source_path = _module_python_path(module)
    source = git.text(revision, source_path, required=False)
    if source is None:
        source_path = f"{module.replace('.', '/')}/__init__.py"
        source = git.text(revision, source_path, required=False)
    if source is None:
        return None
    tree = _parse_python(source, source_path, candidate=candidate)
    bindings = _static_import_bindings(tree, known_modules)
    import_counts = _top_level_import_binding_counts(tree)
    local_counts = _top_level_local_binding_counts(tree)
    total_count = import_counts[symbol] + local_counts[symbol]
    if total_count != 1:
        return None

    direct_binding_nodes = [
        node
        for node in tree.body
        if symbol in _top_level_node_binding_names(node)
        or (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and symbol in _top_level_import_binding_counts(ast.Module(body=[node], type_ignores=[]))
        )
    ]
    if len(direct_binding_nodes) != 1:
        return None
    for node in tree.body:
        if node is not direct_binding_nodes[0] and symbol in _module_scope_rebindings(node):
            return None

    origin = bindings.get(symbol)
    if origin is not None:
        if origin[1] is None:
            # A facade export that is itself a module is outside this symbol proof.
            return None
        return _identity_import_module_symbol_origin(
            git,
            revision,
            origin[0],
            origin[1],
            known_modules,
            candidate=candidate,
            seen=seen | {key},
        )

    if local_counts[symbol] != 1 or import_counts[symbol]:
        return None
    return module, symbol


def _identity_local_import_edges(
    *, path: str, base_tree: ast.Module, head_tree: ast.Module,
    git: _GitObjects, base: str, head: str,
    base_modules: set[str], head_modules: set[str],
) -> set[_ImportEdge]:
    """Check function-local imports in their own static scope."""
    definitions = {
        node.name: node for node in base_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    helpers: dict[str, str] = {}
    for name, definition in definitions.items():
        body = [node for node in definition.body if not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )]
        args = definition.args
        if (not isinstance(definition, ast.FunctionDef) or definition.decorator_list
            or args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg
            or len(body) != 2 or not isinstance(body[0], (ast.Import, ast.ImportFrom))
            or not isinstance(body[1], ast.Return) or not isinstance(body[1].value, ast.Name)):
            continue
        bindings = _static_import_bindings(ast.Module(body=[body[0]], type_ignores=[]), base_modules)
        origin = bindings.get(body[1].value.id)
        if origin is not None and origin[1] is None and _top_level_local_binding_counts(base_tree)[name] == 1:
            if not any(isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(base_tree)):
                helpers[name] = origin[0]

    def scope(tree: ast.Module, definition: ast.FunctionDef | ast.AsyncFunctionDef, *, expand_helpers: bool) -> ast.Module:
        body = copy.deepcopy(definition.body)
        argument_names = {arg.arg for arg in ast.walk(definition.args) if isinstance(arg, ast.arg)}
        imports = [copy.deepcopy(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
        if expand_helpers:
            scoped_helpers = {
                name: module for name, module in helpers.items()
                if name not in argument_names and not any(
                    isinstance(node, (ast.Name, ast.arg)) and (
                        isinstance(node, ast.arg) and node.arg == name
                        or isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))
                    ) for statement in body for node in ast.walk(statement)
                )
            }
            class Expand(ast.NodeTransformer):
                def visit_Assign(self, node: ast.Assign) -> ast.AST:
                    if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                        and node.value.func.id in scoped_helpers and not node.value.args and not node.value.keywords):
                        return ast.copy_location(ast.Import(names=[ast.alias(
                            name=scoped_helpers[node.value.func.id], asname=node.targets[0].id,
                        )]), node)
                    return self.generic_visit(node)
                def visit_Call(self, node: ast.Call) -> ast.AST:
                    if isinstance(node.func, ast.Name) and node.func.id in scoped_helpers and not node.args and not node.keywords:
                        alias = f"_identity_helper_{node.func.id}"
                        if not any(isinstance(item, ast.Name) and item.id == alias for item in ast.walk(base_tree)):
                            imports.append(ast.Import(names=[ast.alias(name=scoped_helpers[node.func.id], asname=alias)]))
                            return ast.copy_location(ast.Name(id=alias, ctx=ast.Load()), node)
                    return self.generic_visit(node)
            body = [Expand().visit(node) for node in body]
        # Parameter shadowing never supplies import evidence.
        for node in imports:
            node.names = [alias for alias in node.names if (alias.asname or alias.name.split('.')[0]) not in argument_names]
        return ast.fix_missing_locations(ast.Module(body=[*imports, *body], type_ignores=[]))

    allowed: set[_ImportEdge] = set()
    for definition in head_tree.body:
        if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)) or definition.name not in definitions:
            continue
        if not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in definition.body):
            continue
        allowed.update(_identity_import_contraction_edges(
            path=path,
            base_tree=scope(base_tree, definitions[definition.name], expand_helpers=True),
            head_tree=scope(head_tree, definition, expand_helpers=False),
            git=git, base=base, head=head, base_modules=base_modules,
            head_modules=head_modules, include_local=False,
        ))
    return allowed


def _identity_import_contraction_edges(
    *,
    path: str,
    base_tree: ast.Module,
    head_tree: ast.Module,
    git: _GitObjects,
    base: str,
    head: str,
    base_modules: set[str],
    head_modules: set[str],
    include_local: bool = True,
) -> set[_ImportEdge]:
    """Permit new edges only for canonical symbols statically used at base."""
    base_imports = _identity_import_bindings(base_tree, base_modules)
    head_imports = _identity_import_bindings(head_tree, head_modules)
    base_counts = _top_level_import_binding_counts(base_tree) + _top_level_local_binding_counts(base_tree)
    head_counts = _top_level_import_binding_counts(head_tree) + _top_level_local_binding_counts(head_tree)
    base_symbols: set[tuple[str, str]] = set()
    for name, old_origin, old_alias in base_imports:
        if base_counts[name] != 1 or _identity_import_name_is_shadowed(base_tree, name, old_alias):
            continue
        symbols = _identity_import_uses(
            base_tree, name, old_origin, git=git, revision=base,
            known_modules=base_modules, candidate=False,
        )
        if symbols:
            base_symbols.update(symbols)

    allowed = _identity_local_import_edges(
        path=path, base_tree=base_tree, head_tree=head_tree, git=git,
        base=base, head=head, base_modules=base_modules, head_modules=head_modules,
    ) if include_local else set()
    if not base_symbols:
        return allowed
    denied: set[_ImportEdge] = set()
    for name, new_origin, new_alias in head_imports:
        source_import = next(
            node for node in head_tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom)) and new_alias in node.names
        )
        single_import = copy.copy(source_import)
        single_import.names = [new_alias]
        edges = set(_import_edges(
            ast.Module(body=[single_import], type_ignores=[]), path,
            known_modules=head_modules,
        ))
        symbols = None
        if head_counts[name] == 1 and not _identity_import_name_is_shadowed(head_tree, name, new_alias):
            symbols = _identity_import_uses(
                head_tree, name, new_origin, git=git, revision=head,
                known_modules=head_modules, candidate=True,
            )
        if symbols and symbols <= base_symbols:
            allowed.update(edges)
        else:
            denied.update(edges)
    allowed.difference_update(denied)
    return allowed


def _identity_normalized_body(
    tree: ast.Module, *, git: _GitObjects, revision: str,
    known_modules: set[str], candidate: bool,
) -> str:
    origins = {
        name: origin
        for name, origin, alias in _identity_import_bindings(tree, known_modules)
        if not _identity_import_name_is_shadowed(tree, name, alias)
    }

    def canonical(module: str, symbol: str) -> str | None:
        origin = _identity_import_module_symbol_origin(
            git, revision, module, symbol, known_modules, candidate=candidate,
        )
        return ".".join(origin) if origin else None

    class Normalize(ast.NodeTransformer):
        def visit_Import(self, node: ast.Import) -> None:
            return None

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            return None

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            if isinstance(node.value, ast.Name) and isinstance(node.ctx, ast.Load):
                origin = origins.get(node.value.id)
                if origin is not None and origin[1] is None:
                    name = canonical(origin[0], node.attr)
                    if name is not None:
                        return ast.Name(id=name, ctx=ast.Load())
            return self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> ast.AST:
            origin = origins.get(node.id)
            if origin is not None and origin[1] is not None and isinstance(node.ctx, ast.Load):
                name = canonical(origin[0], origin[1])
                if name is not None:
                    return ast.Name(id=name, ctx=ast.Load())
            return node

    return ast.dump(Normalize().visit(copy.deepcopy(tree)), include_attributes=False)


def _top_level_node_binding_names(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return set().union(*(_assignment_target_names(target) for target in node.targets))
    if isinstance(node, ast.AnnAssign):
        return _assignment_target_names(node.target)
    return set()


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

# Issue #522: Trusted Uploaded Native Skill Runtime

Date: 2026-07-19

Status: implementation design. This document does not claim review, merge,
deployment, or runtime acceptance.

## Goal

An administrator can upload an ordinary Claude Skill ZIP, review and release
its immutable version, distribute it to users, and run it without rewriting the
package into an ai-platform-specific format. A released uploaded Skill may use
standard local file and command tools inside its run sandbox and may return
ordinary files from `outputs/delivery`.

Input and output declarations are optional product metadata. They may improve
file pickers, cards, and automated acceptance, but they are not an execution
prerequisite.

## Existing Invariants

- The package must contain one root `SKILL.md`; wrapped single-root archives
  are accepted.
- Package bytes are immutable and versioned by the canonical content hash.
- Runs use pinned `run_skill_snapshots`; package or request content cannot
  self-authorize a tool.
- User availability requires both a released version and an applicable tenant,
  department, role, and user distribution decision.
- Runtime tool policy remains zero-click and fail-closed. An allow decision is
  not a filesystem, tenant, credential, or network bypass.
- The SDK runs only inside the real `SandboxRuntime` path. The worker must not
  regain a local runner fallback.
- Existing platform-controlled runners remain server-owned and deterministic.
  An uploaded Skill cannot select one by reusing a builtin Skill ID.
- Public projections never expose package bytes, raw storage keys, host paths,
  callback tokens, model credentials, or executor-private payloads.

## Lifecycle And Trust Decision

The existing version lifecycle is authoritative:

```text
draft -> reviewed -> released -> deprecated/disabled
```

Upload always creates `draft`. The existing reviewed transition is the
administrator trust decision and retains a fail-closed server validation and
audit event. For uploaded Skills that validation proves the immutable package
contract, snapshot hash, manifest identity, path safety, and byte/count limits;
it does not require package authors to include ai-platform SBOM, license,
vulnerability, or review manifests. Existing builtin/G6 governance keeps its
separate evidence policy. The existing promote operation is the only transition
that makes a version `released` and updates rollout policy. A user run can pin
only a released version selected by the server-side release and distribution
decision.

No new trust table or package-declared trust field is introduced. Existing
released uploaded versions remain compatible because their status was created
by an administrator-only route; legacy `active` uploaded versions do not gain
the new native execution profile and must be reviewed through a new immutable
version.

## Execution Profile Module

`app.skills.execution_profiles` is the single deep module for this decision.
Its interface accepts server-owned pinned facts and returns a canonical,
versioned execution profile:

```python
resolve_skill_execution_profile(
    *, skill_id: str, source_kind: str, lifecycle_status: str
) -> SkillExecutionProfile
```

The profile contains only:

- schema version;
- execution strategy;
- trust basis;
- canonical builtin tool identities;
- workspace contract version;
- command-isolation requirement.

Strategies:

| Strategy | Eligible subject | Behavior |
| --- | --- | --- |
| `platform_controlled` | Repository builtin in the server-owned controlled map | Existing deterministic SandboxRuntime runner. |
| `sdk_native` | Reviewed/released uploaded version, or a repository builtin with native local tools | Claude SDK Skill execution in the run sandbox. |
| `sdk_restricted` | Other pinned Skills | Existing declared read-only or Skill-only behavior. |

The canonical `sdk_native` local tool set is exactly:

```text
Skill, Read, Glob, LS, Bash, Write, Edit
```

Network, WebFetch, WebSearch, MCP, browser, Agent/subagent, NotebookEdit, and
all unknown identities remain absent unless a separate server-owned contract
authorizes them.

The profile is embedded in the immutable run manifest. Pin construction,
repository replay validation, worker subject construction, SDK tool
registration, and executor strategy selection all call the same module. A
payload profile that differs from the canonical result fails before execution.

## Credential-Safe Command Execution

The current executor container passes model and callback credentials to the
SDK process. Directly enabling the SDK builtin Bash child would inherit that
environment and violate the product requirement that Skill code cannot read
platform credentials.

Therefore `sdk_native` Bash is admitted only through a per-run sibling tool
container. The credential-bearing SDK executor rewrites every authorized Bash
call to a token-authenticated Unix-domain-socket proxy; the sibling has no
network and no model, callback, executor, storage, or platform credentials.
The sibling starts the command as UID/GID 10001 and must also prove all of
these properties:

- the untrusted command receives a minimal allowlisted environment;
- model, gateway, callback, executor, storage, and platform credentials are
  absent;
- the command cannot inspect the SDK parent process or parent environment;
- the command has no network namespace unless separately authorized;
- the command sees only the current sandbox filesystem and a writable
  workspace;
- the original model-supplied command is transported as data and cannot inject
  into the trusted launcher command;
- timeout, cancellation, process-tree termination, resource limits, and audit
  events remain enforced.

If this isolation cannot be established on the target runtime image, `Bash` is
denied with a stable capability error and Issue #522 cannot be closed. Merely
prefixing a command with `env -i` is insufficient because a same-UID process
can otherwise inspect its credential-bearing parent.

`Read`, `Glob`, `LS`, `Write`, and `Edit` are also parameter-authorized against
the canonical workspace root; parent traversal, `/proc`, container
configuration, internal control paths, and all paths outside the run workspace
fail closed.

## Package And Workspace Contract

The generic ZIP validator, not each Skill, owns package safety:

- reject absolute, drive, UNC, NUL, empty, dot-segment, and case-fold duplicate
  paths;
- reject encrypted, unsupported-compression, symlink, device, FIFO, socket,
  and other non-regular entries;
- bound entry count and individual and total uncompressed bytes;
- require valid UTF-8 `SKILL.md` front matter with a safe name and non-empty
  description;
- preserve all accepted `scripts`, `references`, `assets`, and other regular
  file bytes in the immutable snapshot.

Runtime layout:

```text
/workspace/
├── inputs/                 # authorized files for this run
├── .claude/skills/<id>/    # exact pinned package
├── outputs/delivery/       # user-deliverable files
└── output/                 # bounded legacy compatibility
```

Existing root-level attachment staging remains temporarily available for
controlled-runner compatibility. Native uploaded Skills are instructed to use
`inputs/` and `outputs/delivery/`. ZIP file modes are not trusted or restored;
Skills invoke packaged Python or shell scripts through an explicit interpreter,
so archive metadata cannot create executable, setuid, or other special modes.

## Artifact Collection

The collector accepts regular files only from canonical delivery roots and the
existing legacy root. It rejects symlinks and path escape and enforces per-run
file-count, per-file byte, and total-byte limits.

Known document, spreadsheet, presentation, PDF, text, archive, and image
extensions receive a safe MIME type. Unknown extensions remain
`application/octet-stream` and `runtime_file`. A Skill-specific artifact
manifest is optional; absence of one never prevents collection.

Normal agent completion remains the default success condition for a Skill
without an output declaration. When an optional declaration requires an
artifact, the existing required-artifact gate remains authoritative. UI copy
must distinguish a completed chat response from a produced file.

## Administrator Experience

The existing Skills panel remains the interface. It must expose the minimum
stateful flow without a new page:

1. upload immutable ZIP -> `draft`;
2. review version -> `reviewed`;
3. publish with rollout -> `released`;
4. rollback/deprecate using existing routes.

Actions are role-gated, show current version and lifecycle state, disable while
pending, surface stable server errors, and refresh the authoritative admin
detail after success. The frontend never constructs execution profiles or tool
grants.

## Compatibility And Non-Goals

- Existing builtin controlled Skills keep their commands and required-artifact
  behavior.
- Existing uploaded package bytes and content hashes do not change.
- No database migration, dependency upgrade, per-Skill runner, mandatory
  input/output schema, generic XLSX parser expansion, network/MCP/browser
  grant, Context/Memory expansion, or #509 intent-routing repair is included.
- Marketplace user overlays remain personal overlays and cannot create trusted
  globally runnable versions.

## Verification

Focused source tests must cover:

- lifecycle upload, review, promote, rollout, rollback, and bypass rejection;
- package traversal, special entry, duplicate, encryption, compression, count,
  size, wrapped-root, and byte-preservation cases;
- canonical profile construction and forged/stale/replayed profile rejection;
- uploaded `sdk_native` versus builtin `platform_controlled` strategy,
  including same-ID collision;
- workspace path authorization and credential-safe Bash isolation;
- attachment to Skill script to arbitrary delivery artifact;
- artifact MIME, limits, symlink, and path escape;
- admin lifecycle actions and ordinary-user visibility;
- SDK event evidence across registration, strategy, subprocess, tool event,
  artifact, terminal result, and public projection.

Before commit: changed-scope tests, relevant integration smoke,
`python -m compileall -q app tools scripts`, frontend affected tests,
`git diff --check`, large-feature self-review, and a concise diff summary.

Before merge: one independent fixed-SHA test and one independent high-risk
review recorded on the PR, with every Critical or Important finding resolved.

Runtime acceptance requires exact-main 211 parity plus a real Docker sandbox
run where an administrator-uploaded, reviewed, released native Skill reads a
real attachment, invokes its packaged script with no credential/network access,
writes an arbitrary file to `outputs/delivery`, and produces an authenticated
preview/download artifact for an authorized ordinary user.

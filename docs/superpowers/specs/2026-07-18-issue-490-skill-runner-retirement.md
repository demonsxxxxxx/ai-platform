# Issue #490: Worker-local Skill Runner Retirement

## Decision

`SandboxRuntime` and its executor-owned
`_run_selected_authorized_file_skill` remain the sole authoritative controlled
file-Skill implementation.  The Claude worker and local SDK runner must never
recover an SDK failure by constructing, authorizing, or starting a local file
Skill subprocess.

The retired code is production-unreachable debt: the worker fallback selected
a controlled command only after an empty-Bash SDK failure pattern, while the
SDK runner separately supplied the corresponding local Bash instructions,
canonicalizer, permission allowance, hook, and usage audit.  Keeping these
two halves permits a parallel cross-host implementation and weakens the
single SandboxRuntime boundary.

## Scope

1. Delete the worker-local controlled-runner retry, empty-Bash-loop detection,
   command construction, subprocess invocation, events, and helper-only tests.
2. Delete only the matching local SDK Bash fast-path instructions,
   canonicalization/permission/hook/audit seam, and the four transferred
   legacy tests.  Do not alter Context MCP/session/history handling.
3. Preserve normal SDK failure reporting and all existing sandbox behavior:
   selected-skill authorization, sandbox command/process-tree cleanup,
   cancellation, timeout, required artifacts, target language, and
   user-facing errors.
4. Keep compatibility zero-click write routes and contracts.  The verifier and
   frontend-facing documentation must describe them as no-side-effect `410`
   endpoints; historical reads and terminalization remain available.

## Layered reasoning

| Layer | Required result |
| --- | --- |
| Tool registration | The local Claude SDK no longer exposes a file-Skill Bash fast path. |
| Runner selection | A failed local SDK invocation proceeds to its existing failed result; it cannot select a worker-local runner. |
| Subprocess | Only SandboxRuntime starts the controlled file-Skill subprocess. |
| SDK event | No `controlled_runner_*` event or local fast-path Skill-use audit can be emitted. |
| User-facing error | Existing SDK failure code/message remains intact; retirement does not convert a failure into a misleading success. |

## Compatibility and sunset

Runtime permission request/decision writes were deprecated on `2026-07-17`.
They retain their `410 Gone` compatibility contract and must have no side
effects.  Physical route removal is not part of this issue and is permitted no
earlier than `2026-08-17`, only after a consumer inventory and no-call evidence
are recorded.  Historical redacted reads and safe terminalization of preexisting
records are retained.

## Verification

Run focused worker/SDK boundary, sandbox executor and artifact-contract,
zero-click route/callback, and verifier tests with `--basetemp .pytest-tmp`,
then `python -m compileall -q app tools scripts` and `git diff --check`.
Review the final diff for the fixed scope, absence of secrets, preserved error
paths, test coverage for the retired local path, and no Context or public
contract change.

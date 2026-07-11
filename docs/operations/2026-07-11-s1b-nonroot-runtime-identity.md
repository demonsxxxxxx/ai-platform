# S1B-B Non-Root Runtime Identity And Workspace Permissions

Status: `local partial`

Initial authoritative source base: `c854085f916748ca3c34c8a01bfc6a505b8dca5b` (PR #393 merge)

Current rebased source base: `b7c8d058b9e6dc5626cc71e97b36b4db9fe4126a`
(`origin/main`, PR #396 merge of exact frontend head `1d6b88f06ac902aaff96d3d5573e5ee3ce6fb4af`)

Tracking issue: [#394](https://github.com/demonsxxxxxx/ai-platform/issues/394)

Pull request: [#395](https://github.com/demonsxxxxxx/ai-platform/pull/395) (draft while fresh fixed-head review is pending)

This document tracks only S1B-B. It does not claim S1B, B2, G7, 211, deployment,
runtime acceptance, or gate closure.

## Phase Status

- [x] Phase 1 - Fresh fetch/readback and current source investigation completed in an isolated clean worktree.
- [x] Phase 2 - Fixed `10001:10001` design approved; formal design and implementation plan recorded.
- [x] Phase 3 - TDD RED captured missing workspace module, image/compose identity, authenticated executor identity endpoint, Docker fail-closed ownership, OpenSandbox identity denial, workspace hard-link/mode, and worker TMPDIR contracts.
- [x] Phase 4 - GREEN implementation and focused affected tests completed locally: `543 passed, 3 skipped`.
- [x] Phase 5 - Compile, diff, 20-file approved projection-fix scope, new-line secret, and forbidden-config gates completed locally.
- [ ] Phase 6 - Reviews through rebased head `8a46a69` completed; its projection-leak Important is fixed and fresh fixed-head re-review is pending.
- [ ] Phase 7 - Draft PR #395 exists; `8a46a69` review/CI is historical, and current exact-head comments, required CI, and ready state are pending.
- [~] Docker-capable image/runtime and 211 acceptance are controller-owned and deferred until source stabilizes.

## Approved Contract

- Runtime UID/GID is fixed at `10001:10001` and cannot be changed through ordinary environment configuration.
- API, worker, Docker executor, and OpenSandbox executor must prove that exact business-process identity.
- A narrow one-shot initializer may migrate only root-owned or target-owned entries in the fixed workspace mount.
- Docker and OpenSandbox providers fail closed when workspace ownership or actual executor process identity is unavailable or mismatched.
- Default compose remains free of Docker socket mounts and privileged mode; only the explicit sandbox overlay may grant the worker the socket group.
- Existing S1B-A real-sandbox execution and permission broker semantics remain unchanged.

## Runtime Evidence Boundary

Local source tests cannot prove the current owner of the existing 211 volume,
built-image metadata, compose process identity, actual `id -u`/`id -g`, Docker or
OpenSandbox mount semantics, workspace I/O, cancel cleanup, or 211 source/runtime
parity. The controller must record those checks after the source and PR head stabilize.

## Local TDD Evidence

Observed RED boundaries:

- workspace permission module absent during collection;
- fixed image/compose user and narrow initializer absent;
- runtime identity endpoint returned `404` instead of credential enforcement;
- Docker owner failures silently reached create and provider constructors lacked an identity probe;
- OpenSandbox accepted a root identity probe;
- unsafe mode/hard-link metadata and runtime-owned worker TMPDIR were not enforced.

The first fixed-SHA review regressions were replayed against implementation SHA
`4dc438f` using the committed regression tests from `a527611`. From the detached
`s1b-b-red-source` worktree, the exact command was:

```text
$env:PYTHONPATH = (Get-Location).Path; python -m pytest ..\s1b-b-red-tests\tests\test_runtime_workspace_permissions.py::test_workspace_migration_uses_verified_inode_handle_not_name ..\s1b-b-red-tests\tests\test_runtime_workspace_permissions.py::test_workspace_migration_rejects_link_count_change_before_fchown ..\s1b-b-red-tests\tests\test_sandbox_container_provider.py::test_docker_provider_rejects_cached_reuse_for_same_run_under_different_current_scope ..\s1b-b-red-tests\tests\test_sandbox_container_provider.py::test_docker_cached_reuse_cleans_up_when_executor_url_wait_is_cancelled ..\s1b-b-red-tests\tests\test_sandbox_container_provider.py::test_opensandbox_rejects_cached_reuse_for_same_run_under_different_current_scope ..\s1b-b-red-tests\tests\test_sandbox_container_provider.py::test_opensandbox_cached_reuse_revalidates_remote_scope_metadata -q --rootdir . --basetemp .pytest-tmp\s1b-b-fixed-sha-review-red-002
```

It produced `6 failed`: name-based chown was invoked, link-count change did not
raise, Docker and OpenSandbox current-scope mismatches did not raise, cached
Docker cancellation did not stop the container, and OpenSandbox remote metadata
mismatch did not raise. Both detached replay worktrees were then removed.

The evidence re-review's cleanup-retention finding was captured with these
exact local RED commands before implementation:

```text
python -m pytest tests/test_sandbox_container_provider.py::test_docker_cached_scope_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed tests/test_sandbox_container_provider.py::test_opensandbox_cached_scope_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed -q --basetemp .pytest-tmp\s1b-b-cleanup-red-002
python -m pytest tests/test_sandbox_container_provider.py::test_docker_cached_identity_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed tests/test_sandbox_container_provider.py::test_opensandbox_cached_identity_mismatch_retains_tracking_when_cleanup_cannot_be_confirmed -q --basetemp .pytest-tmp\s1b-b-cleanup-identity-red-001
```

Each command produced `2 failed`: cleanup failures were downgraded to ordinary
start failures and cached lease/sandbox tracking was discarded. The fixed
provider now returns a verifiable cleanup result, raises typed
`container_cleanup_failed`, and retains maintenance-visible tracking when
termination cannot be confirmed. Cached scope/identity, cold identity, and
explicit stop failure paths have Docker and OpenSandbox regressions.

The rebased `8a46a69` evidence review found that provider identity evidence was
also copied into persisted lease labels. The exact RED command was:

```text
python -m pytest tests/test_sandbox_container_provider.py::test_docker_provider_uses_and_verifies_exact_runtime_identity tests/test_sandbox_container_provider.py::test_opensandbox_provider_maps_lease_and_platform_controls tests/test_sandbox_runtime.py::test_runtime_default_db_record_persists_trusted_opensandbox_runtime_handle -q --basetemp .pytest-tmp\s1b-b-projection-red-001
```

It produced `3 failed`: Docker and OpenSandbox returned identity labels in the
lease, and runtime persistence copied all four identity fields. Provider leases
and runtime persistence now filter `ai-platform.executor.*`, while Docker and
OpenSandbox remote metadata retain exact identity labels for reuse validation.
The focused GREEN command added both remote-label mismatch regressions and
completed with `5 passed` under `.pytest-tmp\s1b-b-projection-green-001`.

Observed focused GREEN results so far:

- workspace/launch/provider/executor/contracts: `140 passed`;
- worker-main heartbeat and maintenance tests: `27 passed`.
- pre-review affected provider/runtime/launch/worker/source-authority slice: `514 passed, 3 skipped`.

The final post-cleanup-review affected command was:

```text
python -m pytest tests/test_runtime_workspace_permissions.py tests/test_runtime_launch_script.py tests/test_source_authority_docs.py tests/test_sandbox_container_provider.py tests/test_sandbox_executor_app.py tests/test_sandbox_contracts.py tests/test_sandbox_workspace_manager.py tests/test_sandbox_runtime.py tests/test_sandbox_runtime_cleanup.py tests/test_execution_boundary.py tests/test_claude_agent_worker_adapter.py tests/test_worker.py tests/test_worker_main.py tests/test_admin_runtime_routes.py -q --basetemp .pytest-tmp\s1b-b-post-cleanup-final-001
```

It completed with `541 passed, 3 skipped`. The accompanying
`python -m compileall -q app tools scripts` completed with exit code 0.

After PR #396 moved `origin/main`, the clean local head `4c11f9e` was rebased
without conflict onto `b7c8d058`. The rebased affected command was:

```text
python -m pytest tests/test_runtime_workspace_permissions.py tests/test_runtime_launch_script.py tests/test_source_authority_docs.py tests/test_sandbox_container_provider.py tests/test_sandbox_executor_app.py tests/test_sandbox_contracts.py tests/test_sandbox_workspace_manager.py tests/test_sandbox_runtime.py tests/test_sandbox_runtime_cleanup.py tests/test_execution_boundary.py tests/test_claude_agent_worker_adapter.py tests/test_worker.py tests/test_worker_main.py tests/test_admin_runtime_routes.py -q --basetemp .pytest-tmp\s1b-b-rebase-final-001
```

It completed with `541 passed, 3 skipped`; the parallel compile command exited
0. This is rebased local source evidence only, before final exact-head review,
PR comments, and required final-head CI.

After the projection fix, the same affected file list was run with basetemp
`.pytest-tmp\s1b-b-projection-final-001` and completed with
`543 passed, 3 skipped`; compile again exited 0. All review and CI evidence for
`8a46a69` is historical and cannot satisfy the resulting fixed head.

The fixed-SHA `4dc438f` security and evidence reviews found no Critical issue.
They identified two Important gaps: cached lease reuse was not bound to the
current request/workspace scope, and name-based ownership migration left a
TOCTOU window after validation. The follow-up implementation now binds cached
reuse to the full current scope, revalidates OpenSandbox remote metadata,
cleans up cancelled cached Docker URL discovery, and holds/fstats/fchowns each
validated inode. Regression coverage also exercises malformed cached identity,
missing cached OpenSandbox credentials, target-owned migration no-op, and the
initializer's migrate/drop/probe/close ordering. Fresh review of the resulting
fixed branch head found the source fixes sound, while evidence review identified
that failed cleanup could discard local tracking. That path is now typed, fail
closed, and covered as described above. Fresh review of the new fixed branch
head remains required before Phase 6 can complete.

`python -m compileall -q app tools scripts` exited 0. `git diff --check`,
the approved changed-file scope check, new-line secret scan, and checks forbidding
default Docker socket, privileged mode, runtime UID/GID environment overrides,
and `chmod 777` exited 0.

These are local source results only. Fresh fixed-head re-review, PR evidence, required CI,
Docker-capable runtime evidence, and 211 acceptance remain pending.

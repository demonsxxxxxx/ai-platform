# S1B-B Non-Root Runtime Identity And Workspace Permissions

Status: `local partial`

Authoritative source base: `c854085f916748ca3c34c8a01bfc6a505b8dca5b` (`origin/main`, PR #393 merge)

Tracking issue: [#394](https://github.com/demonsxxxxxx/ai-platform/issues/394)

This document tracks only S1B-B. It does not claim S1B, B2, G7, 211, deployment,
runtime acceptance, or gate closure.

## Phase Status

- [x] Phase 1 - Fresh fetch/readback and current source investigation completed in an isolated clean worktree.
- [x] Phase 2 - Fixed `10001:10001` design approved; formal design and implementation plan recorded.
- [x] Phase 3 - TDD RED captured missing workspace module, image/compose identity, authenticated executor identity endpoint, Docker fail-closed ownership, OpenSandbox identity denial, workspace hard-link/mode, and worker TMPDIR contracts.
- [x] Phase 4 - GREEN implementation and focused affected tests completed locally: `514 passed, 3 skipped`.
- [x] Phase 5 - Compile, diff, 16-file scope, new-line secret, and forbidden-config gates completed locally.
- [ ] Phase 6 - Fixed-SHA independent security and evidence reviews pending.
- [ ] Phase 7 - Ready PR, exact-head GitHub evidence comments, and required CI pending.
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

Observed focused GREEN results so far:

- workspace/launch/provider/executor/contracts: `140 passed`;
- worker-main heartbeat and maintenance tests: `27 passed`.
- final affected provider/runtime/launch/worker/source-authority slice: `514 passed, 3 skipped`.

`python -m compileall -q app tools scripts` exited 0. `git diff --check`,
the approved changed-file scope check, new-line secret scan, and checks forbidding
default Docker socket, privileged mode, runtime UID/GID environment overrides,
and `chmod 777` exited 0.

These are local source results only. Fixed-SHA reviews, PR evidence, required CI,
Docker-capable runtime evidence, and 211 acceptance remain pending.

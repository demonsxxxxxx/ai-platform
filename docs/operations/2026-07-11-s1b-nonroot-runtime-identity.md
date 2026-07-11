# S1B-B Non-Root Runtime Identity And Workspace Permissions

Status: `source design approved`

Authoritative source base: `c854085f916748ca3c34c8a01bfc6a505b8dca5b` (`origin/main`, PR #393 merge)

Tracking issue: [#394](https://github.com/demonsxxxxxx/ai-platform/issues/394)

This document tracks only S1B-B. It does not claim S1B, B2, G7, 211, deployment,
runtime acceptance, or gate closure.

## Phase Status

- [x] Phase 1 - Fresh fetch/readback and current source investigation completed in an isolated clean worktree.
- [x] Phase 2 - Fixed `10001:10001` design approved; formal design and implementation plan recorded.
- [ ] Phase 3 - TDD RED for workspace migration, image/compose, Docker, OpenSandbox, and projection contracts pending.
- [ ] Phase 4 - GREEN implementation and focused affected tests pending.
- [ ] Phase 5 - Compile, diff/scope/secret gates pending.
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

# S1B-C Docker Executor Published-Port Reachability

Status:

- [x] Generation 2 scope envelope - controller `controller-20260711-1945`, board revision 5, dispatch `dispatch-s1b-c-executor-endpoint-20260711-g2-p2`, and both declared fingerprints recomputed exactly before expansion.
- [x] Phase 0 - authoritative base read back as `d5dfa8a340008dfa8fa4fd44102facaed8c42b95`; `HEAD` equals current `origin/main`.
- [x] Phase 1 - root cause and endpoint-contract design confirmed from source plus the main controller's 211 reproduction evidence.
- [x] Phase 2 - RED observed for missing resolver/pinned endpoint (9 failures) and wildcard fallback/env-template gaps (1 failure each).
- [x] Phase 3 - GREEN implementation pins one validated endpoint contract through Docker create, inspect, and probe; provider tests: 105 passed.
- [x] Phase 4 - generation 2 affected provider/client/runtime/runtime-launch/source-authority tests: 223 passed; compile and diff checks exit 0; scope and added-lines secret/path scans clean. Ruff was unavailable in the repository Python environment and is not claimed.
- [ ] Phase 5 - fixed-SHA independent security review and fresh re-review pass.
- [ ] Phase 6 - ready PR carries exact-head review substitute, validation evidence, and required green CI.
- [~] 211 runtime verification - deferred to the main controller; this worktree must not connect to or modify 211.

## Boundary

Tracking issue: [#397](https://github.com/demonsxxxxxx/ai-platform/issues/397).

This source slice fixes only the Docker executor control-plane published-port contract. It does not change executor credentials, runtime identity, callback semantics, non-root execution, read-only filesystem, capability dropping, `no-new-privileges`, default-deny egress, worker code, runtime schemas, Dockerfiles, dependencies, or database state. It must not use wildcard port binding, host networking, privileged containers, relaxed authentication, or a real deployment `.env`.

Authoritative changed-file set:

- `app/runtime/sandbox/container_provider.py`
- `tests/test_sandbox_container_provider.py`
- `deploy/ai-platform/.env.example`
- `tests/test_runtime_launch_script.py`
- `docs/operations/2026-07-11-s1b-docker-executor-reachability.md`

`app/settings.py` and `deploy/ai-platform/docker-compose.sandbox.yml` remain unchanged unless tests prove the existing `SANDBOX_EXECUTOR_PUBLISHED_HOST` setting and base-compose `host.docker.internal:host-gateway` mapping cannot express the contract.

## Root Cause And Data Flow

The base compose config sets `SANDBOX_EXECUTOR_PUBLISHED_HOST=host.docker.internal`, but `DockerContainerProvider.create_or_reuse()` creates every executor with `ports={"18000/tcp": ("127.0.0.1", None)}`. Docker inspect therefore reports `HostIp=127.0.0.1`, and `_published_executor_url_from_container()` returns `http://127.0.0.1:<random-port>`. A health probe from the worker container targets the worker's own loopback rather than the Docker host, so executor creation times out and cleanup removes the container before a lease is stored.

The missing invariant is that configured published hostname, resolved bind IP, Docker create arguments, inspected `HostIp`, and returned probe URL must represent one validated endpoint contract.

## Design

Resolve `sandbox_executor_published_host` once at the start of each cold-create or reuse attempt. Accept exactly one IPv4 address. Reject empty values, unspecified addresses (`0.0.0.0` and `::`), resolution failures, and multiple distinct addresses. Literal or resolved loopback is allowed only when the configured host itself is a loopback literal or `localhost`; a non-loopback hostname resolving to loopback fails closed.

The resolved IP is pinned for the whole operation. Docker create binds the random port only to that IP. Docker inspect must report exactly that pinned IP; empty, wildcard, or different `HostIp` is rejected. The worker-facing URL retains the configured hostname and inspected random port, so containerized mode returns `http://host.docker.internal:<port>` while binding only the host-gateway IP. No second DNS lookup occurs inside the operation. On reuse, a fresh contract is resolved and compared with the existing inspected binding, so DNS drift cannot silently accept the old endpoint.

This is preferred over separate bind-host and published-host settings because two independently configured values can drift. It is preferred over deriving the URL from inspect because inspect alone cannot prove worker reachability or reject a stale loopback binding.

## TDD Implementation Plan

### Task 1: Endpoint resolution and validation

- [x] Add focused tests for loopback, the single host-gateway IPv4 case, empty/wildcard/unresolvable/multiple-address inputs, and loopback mismatch.
- [x] Run each new test group with a fresh child of `.pytest-tmp/` and observe failure caused by the missing contract.
- [x] Add an immutable internal endpoint value carrying `published_host` and `bind_ip`, plus a resolver that returns it or raises `ContainerStartFailedError` without exposing resolver details.
- [x] Re-run the focused tests and preserve one DNS resolution per operation.

### Task 2: Docker create, inspect, and reuse

- [x] Add RED tests proving exact create `ports`, hostname-preserving URL, inspect HostIp equality, old-loopback reuse rejection, DNS-drift rejection, authenticated health and identity probes, and cleanup on rejection.
- [x] Pass the pinned endpoint to Docker create and `_wait_for_executor_url`; validate inspect before returning a URL.
- [x] Re-run provider tests, including cancel and cleanup regressions, without local Docker.

### Task 3: Deployment source contract

- [x] Add `SANDBOX_EXECUTOR_PUBLISHED_HOST=host.docker.internal` to the non-secret env template.
- [x] Extend runtime-launch/provider tests to require the published hostname and existing host-gateway mapping together, and to reject privileged/host-network expansion.

### Task 4: Verification and delivery

- [x] Run focused provider, runtime-launch, and source-authority tests with fresh `.pytest-tmp/<run>` children.
- [x] Run `python -m compileall -q app tools scripts`, `git diff --check`, changed-file scope, and secret/personal-path checks.
- [ ] Commit the verified slice, obtain fixed-SHA independent security review, fix findings, and obtain a fresh re-review.
- [ ] Open a ready PR without merging, post exact-head review-substitute and validation-evidence comments, and read back required CI.

## RED Matrix

| Contract | Expected RED evidence |
| --- | --- |
| Local mode | Loopback bind and loopback URL must remain identical. |
| Container mode | `host.docker.internal` resolves once to one host-gateway IPv4, create binds that IP, and URL retains the hostname. |
| Split prevention | A loopback or other inspected HostIp differing from the pinned bind IP is rejected and cleaned up. |
| Fail closed | Empty, `0.0.0.0`, `::`, unresolvable, multiple-address, and hostname-to-loopback mismatch inputs fail before Docker create. |
| Exact Docker API | `ports` is exactly `{"18000/tcp": (<pinned-ip>, None)}`. |
| Reuse | Existing HostIp, URL hostname, credential, health, and runtime identity are revalidated; stale `127.0.0.1` is not reused. |
| Security posture | No `privileged`, host network, wildcard bind, auth bypass, or relaxed executor security kwargs are introduced. |
| Lifecycle | Cancellation, failed health/identity, stop, remove, and tracked-cleanup behavior remain covered. |

## 211 Reverification Handoff

After merge and deployment by the main controller, submit an ordinary authenticated run using nested `selected_skill={general-chat,0.1.0}`. Confirm Docker inspect reports the exact host-gateway IP rather than loopback or wildcard, the worker health and authenticated runtime-identity probes use `http://host.docker.internal:<random-port>`, a lease is written, and the run advances beyond executor creation. Re-run cancellation and cleanup checks, then remove unreferenced old deployment images and dangling images under the repository's deployment rules.

## Independent Security Review

Fixed-SHA review of `0747af3615eeb0fdd2fc7956b08903cf7b23dc11` found no Critical issues and three Important issues:

- [x] Reject public/global bind addresses; loopback or private IPv4 only.
- [x] Require exactly one inspected binding with an exact HostIp and numeric port in `1..65535`.
- [x] Ensure authenticated health, identity, and later executor task connections use the pinned IP while preserving the configured logical hostname. Generation 2 authorized `app/runtime/sandbox/executor_client.py` and its direct tests without changing runtime or shared lease contracts.

The first two findings were implemented as a separate follow-up change with RED observed for all eight adversarial cases. The third uses private, non-persisted executor header metadata to carry the pinned connect base URL: request preparation pops that metadata before constructing outgoing headers, connects to the private IP, and sets only the logical hostname and port as HTTP `Host`. RED was observed independently for health, runtime identity, and task dispatch, plus unsafe metadata rejection with zero dispatch. Phase 5 remains open until a fresh fixed-head independent re-review finds no unhandled Critical or Important issues.

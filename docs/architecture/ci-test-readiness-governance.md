# CI, Test, and Readiness Governance

Status: normative repository contract
Owner: platform architecture

## 1. Purpose

This contract separates source, test, package, deployment, and runtime proof. It
governs required GitHub checks, test ownership, runtime readiness, offline
verification, and retirement of obsolete checks. It is not a project status
ledger and does not record current pull requests, issue numbers, commits, or
pending cleanup work.

## 2. Evidence levels

Every report names only the level it actually proves:

| Level | Proves | Does not prove |
| --- | --- | --- |
| Source | code, schema, and static contract exist at an exact commit | test execution, deployment, or runtime behavior |
| Focused test | named tests passed against a named source tree | unrelated tests, packaged image, or deployed service |
| CI/build | named job commands passed for an exact workflow subject | rollout, production configuration, or external behavior |
| Packaged image | exact image built, inspected, and started under the named probe | production deployment or real provider acceptance |
| Deployment | exact image and configuration were applied to a named environment | end-user acceptance |
| Runtime | a named live subject returned observed runtime signals | broad release readiness or external acceptance |
| External acceptance | a documented actor completed a named end-to-end workflow | untested tenants, providers, or operating conditions |

Historical files under `docs/release-evidence/` prove only their recorded
subjects. They are never current merely because they remain in the checkout and
must not be scanned by a live health endpoint.

## 3. Test model

Each maintained test has one owner and one class:

- **unit**: deterministic and independent of network, database, Redis,
  containers, wall clock, or repository checkout;
- **contract**: stable API, event, projection, port, facade, or adapter contract;
- **integration**: a real PostgreSQL, Redis, object storage, SDK/provider, image,
  or concurrency boundary;
- **architecture/governance**: dependency, placement, workflow, and trusted-base
  policy; or
- **external acceptance**: a controlled-host or deployed end-to-end procedure.

A required integration test receives its real dependency and completes with zero
skips. A missing service is a failure, not a skip. Opt-in local integration may
skip only when its result is not cited as required evidence.

Delete a test when its production path is retired and it protects no current
wire, persistence, security, denial, concurrency, or compatibility behavior.
Move flat tests with their owning production slice instead of preserving a
historical file layout.

The normative local execution procedure is
[`../agent-rules/local-test-execution.md`](../agent-rules/local-test-execution.md).
Local checks provide developer feedback; required CI owns trusted merge
evidence.

## 4. Required CI topology

Required checks keep stable aggregate names while internal work runs in parallel:

```text
trusted governance (accepted base authority)

backend validation
|-- backend test shards
|-- PostgreSQL/Redis integration (real services; zero skips)
|-- Agent/Skill contracts (real PostgreSQL)
`-- packaged backend image verification
    `-- backend required
```

Rules:

1. Trusted governance executes accepted authority code against the exact range.
2. Candidate workflow tests are regression aids, never their own trust root.
3. Product tests do not wait for author-written review metadata.
4. Required jobs use exact source SHAs, locked dependencies, bounded timeouts,
   and ordinary failure propagation.
5. `continue-on-error`, hidden selectors, and required-service skips are
   forbidden.
6. A test appears in one required lane unless another lane verifies an
   independent subject.
7. Integration lanes write JUnit XML and run
   `tools/require_zero_junit_skips.py` against the exact report.
8. Image jobs retain a stable successful result when an exact diff is
   `not_affected`; they do not disappear from branch protection.
9. CI success is CI evidence only.

## 5. Runtime readiness boundary

Code in `app/` exposes readiness only when it observes the running process or an
authoritative runtime dependency. Valid signals include database/schema state,
Redis queues and leases, worker heartbeats, tenant-scoped operational summaries,
provider/container observations, enforced limits, and per-run control state.

The following stay outside live request paths:

- source-tree or Git-history scans;
- scans of `docs/release-evidence/`;
- source marker and project-closure discovery;
- Markdown report rendering;
- operator checklists or release packets; and
- static control inventories presented as current runtime facts.

Offline checks belong under `tools/` or `scripts/`. A runtime endpoint may link
to an operator command but does not execute or project its historical output.

## 6. Retirement and ownership

When deleting a test or readiness surface:

1. inventory direct, dynamic, deployment, CLI, documentation, and external
   consumers;
2. identify the current runtime replacement or prove none exists;
3. name the replacement test, runtime signal, or operator command;
4. delete obsolete implementation, tests, selectors, and allowlist entries
   together; and
5. run focused tests plus applicable architecture and workflow checks.

Current work, owners, exceptions, and completion state belong in the active pull
request or task, not this durable contract. A new governance rule must replace
or consolidate an existing rule, name a deterministic detector and owner, and
show that a less restrictive test or platform control cannot protect the same
boundary.

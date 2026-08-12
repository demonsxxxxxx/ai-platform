---
status: accepted
decision_issue: 962
---

# Organize the backend as a domain-first modular monolith

## Context

The backend grew around technical surfaces and delivery phases. At the decision
baseline `5176578bed089b7b479cefc528b240c2edf94c31`, `app/` has 95 root
entries, including 84 root-level Python modules. `app/repositories.py` contains
11,945 lines across unrelated domains, `app/models.py` mixes wire and
persistence contracts, and routes, workers, runtime adapters, compatibility
paths, readiness checks, and evidence helpers do not have one placement rule.

This makes apparently simple cleanup unsafe. A zero-reference Python function
may still be a configured process entrypoint. A second repository module may be
an intended facade, or it may be a second business authority. Moving one
function out of a hot file can create another generic bucket instead of a stable
owner. Keeping every old name "for compatibility" makes the temporary boundary
permanent.

The repository therefore needs a target architecture before broad movement or
deletion of production code.

## External patterns considered

The following official upstream sources were reviewed for structural lessons;
they are not AI Platform authorities and their layouts are not copied:

- [Prefect](https://github.com/PrefectHQ/prefect/tree/0b9875b5b40ca0ed1fd18632ef3b306e266a0803/src/prefect)
  at `0b9875b5b40ca0ed1fd18632ef3b306e266a0803` shows
  capability-oriented packages, explicit runtime surfaces, and plugin loading.
- [Sentry](https://github.com/getsentry/sentry/tree/ebe0ada9572d934e53c71ec786667093ec369295/src/sentry)
  at `ebe0ada9572d934e53c71ec786667093ec369295` shows that
  a large product can remain a monolith while grouping substantial behavior by
  product domain.
- [Home Assistant Core](https://github.com/home-assistant/core/tree/0509587fd6c690a5908f80ac639293bc67f348a1/homeassistant)
  at `0509587fd6c690a5908f80ac639293bc67f348a1`
  shows a small core plus explicitly loaded components and manifests.
- [FastAPI's full-stack template](https://github.com/fastapi/full-stack-fastapi-template/tree/c350936d2888ef16ff4f5549684fd8db54935a89/backend/app)
  at `c350936d2888ef16ff4f5549684fd8db54935a89`
  is a useful small-service transport/configuration baseline, but its global
  CRUD/model layout is not a sufficient target for this backend.
- [Langfuse](https://github.com/langfuse/langfuse/blob/803cd0f4a73839d2d24bf1cb8348c4375f784f8c/CONTRIBUTING.md)
  at `803cd0f4a73839d2d24bf1cb8348c4375f784f8c`
  makes web, worker, queue, database, and object-storage runtime boundaries
  explicit.

The adopted design borrows domain ownership, explicit registries, and runtime
separation. It does not adopt thousands of provider packages, import-time
plugin side effects, another project's historical package granularity, or a
framework-first global CRUD layer.

## Decision

AI Platform is a domain-first modular monolith with explicit process and
infrastructure adapters.

1. Top-level product packages represent bounded business contexts, not HTTP,
   ORM, SDK, or delivery-phase layers.
2. A domain may contain only the layers it needs. When present, dependencies
   point from `transport` to `application` to `domain`; infrastructure
   implements application-owned ports and is wired only by `bootstrap`.
3. Cross-domain Python calls use the owning domain's `api.py`. Asynchronous
   integration uses versioned events in `events.py`. Importing another domain's
   internal modules is forbidden.
4. PostgreSQL repositories, Redis transports, object-storage adapters, Harness
   adapters, and Sandbox providers are adapters, not business authorities.
5. Existing runtime and product authority documents remain authoritative. This
   decision governs source placement and dependency direction only.
6. Compatibility code is a measured migration boundary, not a permanent second
   API. It contains no independent business decision and is removed when its
   evidence-based exit condition is met.
7. Production code is removed only after the deletion proof appropriate to its
   real invocation surface. Static zero-reference searches alone are not enough
   for modules, routes, CLIs, dynamic loaders, persisted state, or deployment
   entrypoints.
8. Migration uses a strangler sequence: define the canonical owner, replay the
   same behavior against it, leave a thin facade only for proven consumers,
   migrate callers, then delete the facade. Movement and behavior change are
   separate review subjects.

The complete normative contract, target tree, naming rules, deletion proof,
and current-to-target mapping live in
[`../architecture/source-code-architecture.md`](../architecture/source-code-architecture.md).

## Consequences

- New backend behavior has one obvious domain owner and one allowed dependency
  direction.
- The API, worker, executor, and maintenance entrypoints are explicit process
  surfaces that share one reviewed codebase and domain model. Separating their
  deployment requires the applicable runtime authority and acceptance evidence.
- `app/routes`, `app/models.py`, and `app/repositories.py` become migration
  surfaces rather than locations for new unrelated behavior.
- Some duplication remains temporarily while callers are migrated. Temporary
  facades must be thinner than the canonical implementation and cannot write or
  decide independently.
- Architecture checks will initially reject only new violations and growth of
  frozen legacy surfaces. Existing violations are retired through bounded,
  domain-owned PRs instead of a big-bang rewrite.
- A future service extraction is possible at a bounded-context API/event seam,
  but distribution is not introduced until operational evidence justifies it.

## Rejected alternatives

### Keep global technical layers

Continuing to add routes, models, services, and repositories to shared global
modules minimizes short-term movement but preserves the authority ambiguity and
hot-file coupling that triggered this decision.

### Split into microservices now

Network boundaries would add distributed transactions, versioned deployment,
operational ownership, and failure modes before the in-process domains are even
separated. Source modularity is required first; process distribution is a later
evidence-based decision.

### Copy a mature project tree

Prefect, Sentry, Home Assistant, FastAPI, and Langfuse solve different product
and organizational problems. Copying their directory names would reproduce
their history rather than encode this platform's authorities.

### Rewrite the backend into the target tree in one change

A big-bang rewrite would mix moves, semantic changes, compatibility changes,
and deletion. It would make authorization, queue, persistence, and runtime
regressions difficult to attribute or roll back.

### Preserve every old symbol for a calendar window

Time alone does not prove that a consumer exists or has migrated. Internal-only
aliases are migrated atomically. Public, deployment, environment, or persisted
compatibility remains only for a named consumer and an observable exit
condition; a date may cap a migration but cannot justify it.

## Evidence boundary

This ADR and its companion architecture document establish source authority.
Architecture tests can prove imports, placement, facade shape, and candidate
deletion declarations. They cannot prove external consumer migration, a
deployed process graph, queue drain, database upgrade, object-store cleanup, or
runtime behavior. Those require the applicable issue/PR, release, and runtime
evidence.

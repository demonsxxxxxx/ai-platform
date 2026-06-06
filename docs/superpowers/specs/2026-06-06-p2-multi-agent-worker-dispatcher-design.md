# P2 Multi-Agent Worker Dispatcher Design

## Goal

Add a bounded worker-side multi-agent dispatcher that can advance safe ready
parent steps without an operator manually calling the dispatch tick route.

## Current Context

The platform already has admin-only dispatch primitives: readiness projection,
claim, lease cleanup, child handoff, worker reconciliation, parent cancel
propagation, parent terminal rollup, and a one-step dispatch tick route. The
remaining runtime gap is a polling dispatcher that uses those primitives from
the worker loop while preserving the same fail-closed rules.

## Contract

The worker dispatcher is configuration-gated and tenant-scoped. When enabled,
the normal worker parks a top-level multi-agent parent run before adapter
execution by writing a server-owned top-level `multi_agent_dispatch`
`awaiting_dispatch` marker outside user-controlled `input`. Each dispatcher
maintenance pass then finds a small number of same-tenant parked parent runs,
attempts at most one safe ready step dispatch per parent, enqueues the created
child run, and returns a redacted operational summary for logs/tests.

The dispatcher uses a synthetic platform-admin principal with source
`worker_multi_agent_dispatcher` only inside the backend. It does not expose a
new user route, does not let ordinary users bypass the admin-only dispatch
route, and does not read executor private payloads.

## Fail-Closed Rules

- If `MULTI_AGENT_DISPATCH_WORKER_ENABLED` is false or absent, the worker does
  not scan parent runs.
- Invalid interval or limit values disable the dispatcher for that pass.
- Candidate listing only includes same-tenant running top-level multi-agent
  runs with the top-level server-owned `multi_agent_dispatch.orchestration_state =
  awaiting_dispatch` marker.
- User-controlled run and chat input cannot forge server-owned `resume` or
  `multi_agent_dispatch` metadata; ordinary public run projections strip those
  control fields recursively.
- Ordinary public step, event, message, and run input projections strip
  dispatch claim/handoff control metadata such as dispatch ids, dispatch state,
  parent step ids, and copied-run ids.
- A normal leased parent run must park and exit before automatic dispatch can
  target it; the parent adapter must not execute the same step graph in
  parallel with child dispatch.
- Existing readiness and claim validation decide whether a step is safe.
- Non-ready, unsafe, terminal, stale, or conflicted candidates are skipped
  without enqueueing child runs.
- If Redis enqueue fails after the DB handoff commits, the worker performs a
  compensating DB update: the child run is failed, the parent step is reset to
  `pending`, hidden event/audit evidence is written, and the worker maintenance
  pass continues without taking down the worker loop.
- Successful dispatch still uses the existing claim, child handoff, context
  snapshot, release-policy, queue payload, event, and audit paths.

## Boundaries

This slice does not add a new frontend entry, DB migration, new worker process,
high-risk tool execution, Docker sandbox expansion, or external project fact
source. Local Docker remains unsupported; runtime smoke runs only on 211.

## Verification

Focused tests must prove disabled-by-default behavior, interval gating,
malformed and non-finite interval/limit setting fail-closed behavior, candidate
listing SQL scope, top-level-only parent marker writes, forged
control-metadata stripping, ordinary projection stripping for dispatch
control fields, parent parking before adapter execution, successful
claim/handoff/enqueue sequencing, enqueue-failure compensation, and worker loop
ordering. 211 smoke must prove the deployed worker can dispatch one safe
parked parent step when the feature flag is enabled, while ordinary projection
redaction and cleanup remain intact.

# Redis Streams SSE v2.1 Cutover and Acceptance

Status: normative release and evidence contract; no deployment is authorized by
this document

Index: [Redis Streams SSE Event Channel](../architecture/redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns implementation grouping, release-atomic cutover,
negative source checks, SSE gateway configuration, focused local/CI gates, and
External Acceptance. The 211 release procedure remains exclusively owned by
`211-release-operations-runbook.md`.

## Implementation and commit groups

One issue and Draft PR may contain reviewable commits in this order:

1. **Documentation:** ADR 0004, authority index, wire protocol, execution
   control, and this operations contract.
2. **A0/A1/A2 dormant foundation:** envelopes/cursors, deterministic callback
   receipts, Redis pools/bridge, schema/repositories, admission and authorization
   leases. These commits cannot enable Redis-backed admission or remove the old
   path.
3. **B-E release-atomic cutover:** producer/coalescer, Chat XREAD reader,
   terminal publication, frontend accepted cursor/gap/final hydrate, and removal
   of every PostgreSQL delta/live reconnect path. This may be several commits for
   review, but the resulting source set is one release unit.
4. **Tests and gates:** focused fault injection, negative cutover checker, CI and
   release guard updates.

This is commit ordering, not permission to deploy intermediate images. Dormant
foundation must be behaviorally unreachable from production admission.

## Release-atomic rule

B-E is accepted only as a complete set. CI and the release authority must reject
any candidate where exactly one of these old/new behaviors remains:

- SDK/executor assistant deltas can still enter PostgreSQL;
- the Chat stream still reads PostgreSQL events, sleeps/polls, or emits a
  PostgreSQL sequence cursor;
- Redis producer/reader/terminal schema or projection versions disagree;
- frontend can invent a transport ID, omit the accepted Redis cursor, advance
  it before reducer commit, or seed live reconnect from PG status/history;
- a configuration or feature flag can choose old and new live stacks;
- terminal/end can publish before the frozen PostgreSQL intent commits;
- API, worker, executor, or frontend artifacts have different accepted design
  IDs or cutover manifest versions.

The release build contains a single cutover manifest/version consumed by API,
worker, executor, and frontend build provenance. `tools/pre_push_readiness.py`,
the dedicated negative checker, CI, and release preparation verify the complete
set. Intermediate main commits may exist only when production admission is
provably dormant and the release tool rejects their manifest.

Rollback is an immutable prior image with backward-compatible schema. The
current image contains no hidden legacy runtime flag. Active v2.1 runs must
drain, safely pause, or terminalize; pending v2.1 publication intent remains
owned by a v2.1 recovery image and blocks an older image from pretending to
resume its cursor.

## Negative cutover checker

`tools/check_sse_runtime_cutover.py --scope full` is a required source gate. It
uses Python AST/import analysis plus bounded TypeScript/source checks and fails
closed on an unknown scope. Every failure names the file and symbol/data flow.
It rejects:

- `chat_session_stream` calling PostgreSQL event-list/page/fold helpers,
  `asyncio.sleep`, or a status/history live fallback;
- worker or runtime callback `assistant_delta` routing to PostgreSQL append
  functions;
- PostgreSQL sequence/cursor serialization as an SSE ID;
- `XREADGROUP`, in-process replay, or selectable legacy/shadow backends;
- public raw command/tool/reasoning/path/credential event types;
- Redis append without the reviewed atomic TTL-refresh operation;
- mismatched stream schema/projection/cutover manifest constants;
- frontend event-ID UUID fallback, accepted cursor mutation before successful
  reducer commit, reconnect without `Last-Event-ID`, or PG sequence/status/history
  entering the live cursor/fold;
- missing required no-buffer/no-transform gateway configuration;
- an incomplete B-E manifest that release tooling could package.

Checker tests must use structural fixtures and execute the checker. A test that
only searches for one string does not satisfy this gate.

## SSE application and Nginx contract

The application response sets:

```text
Content-Type: text/event-stream
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
Connection: keep-alive
```

The dedicated SSE proxy location enforces:

```nginx
proxy_http_version 1.1;
proxy_buffering off;
proxy_request_buffering off;
proxy_cache off;
gzip off;
proxy_read_timeout <greater than heartbeat plus accepted jitter>;
proxy_send_timeout <bounded slow-consumer deadline>;
add_header X-Accel-Buffering no always;
```

The response is not compressed or transformed. A heartbeat comment occurs
within the accepted read-idle budget and carries no event ID. Slow-consumer
queues have explicit event/byte/deadline bounds and close rather than buffer
without limit.

The existing `frontend/web/nginx.conf.template` already disables proxy and
request buffering for `/api/`; implementation extends it only with missing
cache/compression/header rules and a correctly scoped SSE timeout. It must not
duplicate conflicting locations or weaken other API routes.

ASGI `send` completion is application-to-protocol-server handoff, not proof of
browser receipt. The revocation acceptance boundary is the owned application/
gateway writer and connection close. Real Nginx/browser probes are required to
observe downstream buffering and cannot turn source assertions into a universal
network guarantee.

## Focused local and CI gates

Local verification follows repository rules and never substitutes routine full
pytest for bounded suites. Required affected gates include:

- backend compile/import checks;
- deterministic callback/Redis bridge/coalescer/authorization/terminal unit
  suites under workspace-local `--basetemp .pytest-tmp/...`;
- opt-in real PostgreSQL and real Redis selectors when services are locally
  available, reported as unavailable rather than passed otherwise;
- callback response-loss and Redis unknown-outcome fault injection;
- negative cutover checker and its structural fixture tests;
- frontend SSE parser/handler/reducer tests, scoped lint, TypeScript check,
  projection audit, and production build;
- immutable pre-push readiness from fetched `origin/main` authority for each
  pushed candidate.

CI must run the backend streaming/callback suites and frontend SSE suites for
changes to their production paths. It must build the complete cutover manifest
and run the full negative checker before an image is release eligible.

## External Acceptance

The following evidence cannot be claimed from local mocks, source inspection, or
ordinary CI. It requires exact source SHA, image digests, cutover manifest,
configuration fingerprint, Redis/PostgreSQL versions, API/worker replica counts,
Nginx config, and browser build:

- real Redis plus PostgreSQL with at least two API readers and the intended
  worker/executor topology;
- first-delta/inter-delta p50/p95/p99, reconnect latency, Redis command latency,
  PostgreSQL callback receipt/authorization renewal QPS, pool occupancy, memory,
  and retained replay seconds;
- a 50-concurrent-run harness with workload shape, duration, event rate, entry
  bytes, reconnect/slow-consumer mix, stop conditions, raw counts, and no hidden
  retry of failures;
- two independent readers, reconnect within retained history, forced trim,
  missing key, Redis restart, overlapping native IDs after rebuild, gap and
  durable hydrate;
- Redis unavailable at admission proving zero SDK dispatch; mid-run outage for
  eligible completion, approval/control pause/failure, bounded memory, no PG
  delta fallback, and truthful terminal convergence;
- callback HTTP response loss, duplicate batch conflict, Redis `XADD` unknown
  outcomes, PG terminal rollback, PG commit plus terminal Redis unknown outcome,
  reconciler retry, and exact terminal payload digest;
- authorization renewal/invalidation across API replicas, blocked `XREAD`, slow
  downstream delivery, instance restart/loss, fail-closed uncertainty, and no new
  application/gateway frame after recorded effective boundary;
- Nginx buffering/cache/compression disabled, heartbeat beneath timeout, bounded
  slow client behavior, gateway close on invalidation, and browser-observed
  progressive rendering/final replacement;
- ordinary-user privacy scan proving no raw command/tool payload, hidden
  reasoning, credentials, paths, or storage keys;
- connection/pool cleanup to baseline and immutable-image rollback behavior.

Fifty-concurrency acceptance is a measured result, not inferred from unit tests
or the `50 / 0.04 ~= 1250 frames/s` sizing model. Browser-chain closure requires
the exact deployed subject and cannot be inferred from frontend tests.

## Evidence states

| State | Meaning |
| --- | --- |
| `local partial` | named focused local checks passed on an exact SHA |
| `PR ready` | Draft candidate and named CI evidence are available for review; not merged/deployed |
| `reviewed` | independent fixed-SHA findings are resolved or explicitly accepted under repository policy |
| `External Acceptance pending` | real topology/proxy/browser/load evidence not yet observed |
| `211 verified` | exact deployed subject passed the separately authorized current 211 procedure and required runtime checks |

Never promote one state to another without observing the additional evidence.
No issue auto-closes merely because the Draft PR exists or local tests pass.

## External Acceptance record

The runtime evidence packet records exact subjects, commands/harness version,
raw counts and percentiles, Redis clients/memory, PostgreSQL write/query counts,
cursor/gap/terminal cases, invalidation timestamps and measured boundary, proxy
headers/config, browser result, privacy scan, cleanup, rollback, failures, and
whether any selector was unavailable. Secrets, raw prompts, delta text, and real
environment files are excluded or redacted.

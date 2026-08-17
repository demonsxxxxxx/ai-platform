# Redis Streams SSE v3 Cutover and Acceptance

Status: normative release and evidence contract; no deployment is authorized by
this document

Index: [Redis Streams SSE Event Channel](../architecture/redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns implementation grouping, release-atomic cutover,
negative source checks, SSE gateway configuration, focused local/CI gates, and
External Acceptance. The application release procedure remains exclusively owned by
`release-operations-runbook.md`.

## Implementation and commit groups

One issue and Draft PR may contain reviewable commits in this order:

1. **Documentation:** ADR 0009, authority index, wire protocol, execution
   control, and this operations contract.
2. **Dormant generated protocol:** the single JSON Schema, checked-in generated
   Python/TypeScript artifacts, drift check, and cross-language fixtures. These
   commits cannot change the active v2.1 manifest.
3. **Dormant live fan-out foundation:** Redis append-plus-publish Lua, shared
   process feed, bounded local subscribers, and subscribe-before-replay state
   machine. These commits cannot be selected by production routes.
4. **Release-atomic v3 cutover:** producer/envelope/key prefix, Chat SSE reader,
   terminal publication, generated frontend reducer/controller, and removal of
   the v2.1 per-browser `XREAD` and legacy approval/event paths.
5. **Tests and gates:** focused fault injection, schema drift, negative cutover
   checker, CI, release guard, and External Acceptance harness updates.

This is commit ordering, not permission to deploy intermediate images. Dormant
foundation must be behaviorally unreachable from production admission.

## Release-atomic rule

The release-atomic v3 cutover is accepted only as a complete set. CI and the
release authority must reject any candidate where exactly one of these old/new
behaviors remains:

- the Chat stream still allocates a blocking `XREAD` connection per browser,
  subscribes after replay, or can silently drop a local live notification;
- Redis `XADD`, TTL refresh, and `PUBLISH` are not one checked Lua operation;
- a process creates more than one Redis live subscription for the same active
  Run stream or keeps an unbounded browser queue;
- generated Python/TypeScript artifacts differ from the one v3 JSON Schema;
- SDK/executor assistant deltas can still enter PostgreSQL;
- the Chat stream still reads PostgreSQL events, sleeps/polls, or emits a
  PostgreSQL sequence cursor;
- Redis producer/reader/terminal schema, public event schema, key prefix, or
  projection versions disagree;
- frontend can invent a transport ID, omit the accepted Redis cursor, advance
  it before reducer commit, seed live reconnect from PostgreSQL status/history,
  or retain legacy runtime approval/reasoning stream handlers;
- a configuration or feature flag can choose v2.1/v3 live stacks;
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
  `asyncio.sleep`, a blocking Redis `XREAD`, or a status/history live fallback;
- worker or runtime callback `assistant_delta` routing to PostgreSQL append
  functions;
- semantic producer publication before its safe PostgreSQL row commits, Redis
  calls inside direct or nested transaction helpers, or missing exact
  row-derived identity/sequence/time wiring;
- PostgreSQL sequence/cursor serialization as an SSE ID;
- `XREADGROUP`, per-browser blocking `XREAD`, subscribe-after-replay, in-process
  replay, unbounded local subscriber queues, or selectable legacy/shadow
  backends;
- public raw command/tool/reasoning/path/credential/approval event types;
- Redis append without atomic `XADD` plus TTL refresh plus `PUBLISH`;
- handwritten backend/frontend v3 envelope fields or event enums outside the
  generated artifacts and strict event-specific security projector;
- mismatched stream schema/public schema/projection/cutover manifest constants;
- frontend event-ID UUID fallback, accepted cursor mutation before successful
  reducer commit, reconnect without `Last-Event-ID`, or PostgreSQL
  sequence/status/history entering the live cursor/fold;
- missing required no-buffer/no-transform gateway configuration;
- an incomplete v3 cutover manifest that release tooling could package.

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
within the accepted read-idle budget and carries no event ID. Each browser queue
has explicit event/byte/deadline bounds and closes rather than buffering without
limit. The process-level Pub/Sub feed has explicit disconnect/teardown behavior
and never substitutes process memory for replay.

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
- generated protocol regeneration and cross-language fixtures;
- deterministic callback/Redis append-plus-publish/coalescer/authorization/
  terminal unit suites under workspace-local `--basetemp .pytest-tmp/...`;
- shared-feed attach race, overlap dedupe, disconnect, teardown, and per-browser
  event/byte overflow tests;
- committed semantic producer tests proving the strict Skill/tool execution
  projection reaches the Redis reader contract without raw payload fields;
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
- two independent readers on one API and across API replicas, reconnect within
  retained history, forced trim, missing key, Redis/PubSub restart, attach-time
  publication, overlapping native IDs after rebuild, gap and durable hydrate;
- Redis unavailable at admission proving zero SDK dispatch; mid-run outage for
  eligible completion, bounded memory, no PostgreSQL delta fallback, and
  truthful terminal convergence;
- callback HTTP response loss, duplicate batch conflict, Redis `XADD` unknown
  outcomes, PG terminal rollback, PG commit plus terminal Redis unknown outcome,
  reconciler retry, and exact terminal payload digest;
- authorization renewal/invalidation across API replicas, blocked live wait,
  slow downstream delivery, instance restart/loss, fail-closed uncertainty, and
  no new application/gateway frame after recorded effective boundary;
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
| `runtime verified` | exact deployed subject passed the separately authorized controlled-host procedure and required runtime checks |

Never promote one state to another without observing the additional evidence.
No issue auto-closes merely because the Draft PR exists or local tests pass.

## External Acceptance record

The runtime evidence packet records exact subjects, commands/harness version,
raw counts and percentiles, Redis clients/memory, PostgreSQL write/query counts,
cursor/gap/terminal cases, invalidation timestamps and measured boundary, proxy
headers/config, browser result, privacy scan, cleanup, rollback, failures, and
whether any selector was unavailable. Secrets, raw prompts, delta text, and real
environment files are excluded or redacted.

# Redis Streams SSE v4 Cutover and Acceptance

Status: normative release and evidence contract; no deployment is authorized by
this document

Index: [Redis Streams SSE Event Channel](../architecture/redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns implementation grouping, release-atomic cutover,
negative source checks, SSE gateway configuration, focused local/CI gates, and
External Acceptance. The application release procedure remains exclusively owned by
`release-operations-runbook.md`.

## Implementation and commit groups

One coherent change may contain reviewable commits in this order:

1. **Documentation and protocol:** ADR 0012, authority index, v4 wire,
   execution control, generated schema/types, and this operations contract.
2. **Durable event ownership:** canonical v4 envelopes, transaction-scoped
   stream admission, PostgreSQL publication claims, and transaction-external
   Redis publication with receipt-fenced disposition.
3. **Successor recovery:** PostgreSQL snapshot/claim, inactive Redis candidate
   construction, source-fingerprint and persisted-receipt verification, and
   token-fenced readiness without changing active authority.
4. **Release-atomic v4 cutover:** atomic successor activation, producer and
   cancellation publication, indexed retry maintenance, SSE route/replay, and
   frontend v4 connection/reducer ownership.
5. **Tests and gates:** focused fault injection, schema drift, negative cutover
   checker, required CI service matrices, release guard, and External
   Acceptance harness updates.

This is commit ordering, not permission to deploy intermediate images. Dormant
foundation must be behaviorally unreachable from production admission.

## Release-atomic rule

The release-atomic v4 cutover is accepted only as a complete set. CI and the
release authority reject any candidate where exactly one of these old/new
behaviors remains:

- producer admission can commit a public or terminal event before the same
  transaction prepares stream authority;
- pending public rows, including cancellation and retry-delayed rows, lack one
  production-owned durable drain path;
- Redis publication holds PostgreSQL locks or accepts a blank, malformed, or
  mismatched receipt as success;
- missing terminal history reconstructs the active incarnation, or successor
  activation does not re-lock the Run/current Attempt and compare claim token,
  expiry, source fingerprint, item count, and persisted Redis receipt;
- the Chat stream emits anything other than strict v4 public events and
  controls with the accepted Redis cursor, or replay and live paths use
  different projection rules;
- frontend can invent a transport ID, advance a cursor before reducer or
  terminal-hydration acceptance, disconnect on a valid semantic duplicate, or
  let `stream.end` become a second terminal authority;
- active production code imports the legacy v3 frontend adapter or a selectable
  v3/v4 runtime flag remains;
- generated Python/TypeScript artifacts differ from the one v4 JSON Schema;
- terminal/`stream.end` can publish before the frozen PostgreSQL intent commits;
- API, worker, executor, frontend, workflow, checker, or release documentation
  disagrees on the active v4 design and projection versions.

Release preparation verifies the exact source SHA, immutable API/worker and
frontend image digests, generated v4 protocol artifacts, required workflow
results, and configuration fingerprint. The dedicated negative checker, CI, and
release preparation own those separate facts. Intermediate main commits may
exist only when production admission is provably dormant and release
verification rejects incomplete evidence.

Rollback is an immutable prior reviewed image. The current image contains no
hidden legacy runtime flag. Active v4 work must drain, safely pause, or
terminalize before rollback; a prior image must never reinterpret v4 durable
rows, receipts, successor claims, or cursors as an older protocol.

## Negative cutover checker

`tools/check_sse_runtime_cutover.py --scope full` is a required source gate. It
uses Python AST/import analysis plus bounded TypeScript/source checks and fails
closed on an unknown scope. Every failure names the file and symbol/data flow.
It rejects:

- `chat_session_stream` calling PostgreSQL event-list/page/fold helpers,
  `asyncio.sleep`, a blocking Redis `XREAD`, or a status/history live fallback;
- worker or runtime callback `assistant_delta` routing to a second publisher;
- Redis publication from callback, worker, or nested transaction helpers;
- missing transaction-scoped v4 admission before SDK dispatch or missing the
  single committed-event publication handoff;
- per-browser blocking Redis reads or retired v2.1 stream markers;
- Redis append without atomic `XADD` plus TTL refresh plus `PUBLISH`;
- generated Python or TypeScript v4 artifacts that differ from the one v4 JSON
  Schema;
- frontend event-ID UUID fallback, accepted cursor mutation before successful
  reducer commit, reconnect without `Last-Event-ID`, or an active v3 adapter,
  selector, or fallback in the production connection path;
- runtime approval events entering the public frontend handler; and
- missing required no-buffer/no-transform gateway configuration.

The checker is one source gate, not the complete release evidence collector.
The required workflow separately executes its selected schema-contract, real
Redis/PostgreSQL, frontend projection, and image-provenance owners; the checker
itself owns generated-artifact drift. Standalone owning tests not selected by a
required job remain useful repository evidence but are not represented here as
executed release evidence. Checker tests use structural fixtures and execute the
checker; a test that only searches for one string does not satisfy this gate.

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
  projection audit, and production build.

CI must run the backend streaming/callback suites and frontend SSE suites for
changes to their production paths. It must verify generated v4 protocol
artifacts and run the full negative checker before an image is release eligible.

## External Acceptance

The following evidence cannot be claimed from local mocks, source inspection, or
ordinary CI. It requires exact source SHA, image digests, configuration
fingerprint, Redis/PostgreSQL versions, API/worker replica counts, Nginx config,
and browser build:

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
- authorization-epoch commits across API replicas, blocked live wait, slow
  downstream delivery, instance restart/loss, renewal denial, and no old-epoch
  application/gateway frame after the recorded <=15-second lease deadline;
- Nginx buffering/cache/compression disabled, heartbeat beneath timeout, bounded
  slow client behavior, gateway close on lease expiry, and browser-observed
  progressive rendering/final replacement;
- ordinary-user privacy scan proving no raw command/tool payload, hidden
  reasoning, credentials, paths, or storage keys;
- connection/pool cleanup to baseline and immutable-image rollback behavior.

Fifty-concurrency acceptance is a measured result, not inferred from unit tests
or the `50 / 0.04 ~= 1250 frames/s` sizing model. Browser-chain closure requires
the exact deployed subject and cannot be inferred from frontend tests.

### Point-in-time acceptance procedure

Each execution creates a uniquely identified, immutable evidence packet. The
packet is a time-bounded observation of one deployed subject; it is not a
repository status page and a later execution never opens or overwrites an
earlier packet. Raw private capture stays in a separate restricted staging
directory and is never part of the exportable packet.

1. **Freeze the subject.** Record `observed_at_utc`, the full source SHA, API,
   worker, executor, and frontend image digests, Nginx configuration fingerprint,
   Redis/PostgreSQL versions, replica counts, network/security profile, browser
   build, and the acceptance-harness revision. Stop if any digest or fingerprint
   changes during the run.
2. **Create exclusive staging and packet directories.** Generate a fresh UUID,
   use `mkdir` without `-p`, and stop if either path already exists. Keep
   authentication in an operator-owned curl configuration file with mode `0600`;
   that file is never copied into staging or the packet. Example:

   ```sh
   umask 077
   export SSE_ACCEPTANCE_PACKET_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(uuidgen | tr '[:upper:]' '[:lower:]')"
   export SSE_ACCEPTANCE_STAGE="/var/tmp/ai-platform-sse-private/${SSE_ACCEPTANCE_PACKET_ID}"
   export SSE_ACCEPTANCE_OUT="/var/tmp/ai-platform-sse-acceptance/${SSE_ACCEPTANCE_PACKET_ID}"
   export SSE_ACCEPTANCE_BASE_URL=https://<accepted-host>
   export SSE_ACCEPTANCE_SESSION_ID=<accepted-session-id>
   export SSE_ACCEPTANCE_RUN_ID=<accepted-run-id>
   export SSE_ACCEPTANCE_CURL_CONFIG=/secure/operator/sse-acceptance.curl.conf
   test "$(stat -c %a "${SSE_ACCEPTANCE_CURL_CONFIG}")" = 600
   mkdir "${SSE_ACCEPTANCE_STAGE}" "${SSE_ACCEPTANCE_OUT}"
   chmod 0700 "${SSE_ACCEPTANCE_STAGE}" "${SSE_ACCEPTANCE_OUT}"
   ```

   Record the packet ID in every harness result and manifest. A missing `uuidgen`,
   an unreadable credential file, an existing path, or a mode mismatch stops the
   procedure before any request is sent.
3. **Capture the gateway contract into private staging.** Run `nginx -T` inside
   the exact deployed frontend gateway and fingerprint the capture. For a Docker
   deployment the concrete form is:

   ```sh
   export SSE_ACCEPTANCE_GATEWAY_CONTAINER=<accepted-frontend-container>
   docker exec "${SSE_ACCEPTANCE_GATEWAY_CONTAINER}" nginx -T \
     >"${SSE_ACCEPTANCE_STAGE}/nginx.full.raw" 2>&1
   sha256sum "${SSE_ACCEPTANCE_STAGE}/nginx.full.raw" \
     >"${SSE_ACCEPTANCE_STAGE}/nginx.full.raw.sha256"
   ```

   Kubernetes or systemd deployments must record and run the equivalent exact
   command against the frozen gateway instance. Open one admitted Run and use
   curl tracing so connection and frame arrival times are present in the raw
   evidence while client buffering stays disabled:

   ```sh
   curl --config "${SSE_ACCEPTANCE_CURL_CONFIG}" \
     --no-buffer --fail-with-body --max-time 45 \
     --trace-time --trace-ascii "${SSE_ACCEPTANCE_STAGE}/sse.trace.raw" \
     --dump-header "${SSE_ACCEPTANCE_STAGE}/sse.headers.raw" \
     --output "${SSE_ACCEPTANCE_STAGE}/sse.frames.raw" \
     "${SSE_ACCEPTANCE_BASE_URL}/api/chat/sessions/${SSE_ACCEPTANCE_SESSION_ID}/stream?run_id=${SSE_ACCEPTANCE_RUN_ID}"
   ```

   The recorded response must contain `Content-Type: text/event-stream`,
   `Cache-Control: no-cache, no-transform`, and `X-Accel-Buffering: no`. Record
   connection time, first public delta, each heartbeat, terminal, gateway close,
   and curl exit time from the harness clock. Treat the raw curl trace as
   credential-bearing even when the client masks a header. Expire the short-lived
   acceptance credential immediately after capture and record that revocation in
   the private staging log.
4. **Run one pinned recovery/load harness.** Before execution, record the full
   harness source SHA, executable path, arguments, expected topology, and a
   SHA-256 digest of its machine-readable case inventory. The pinned invocation
   must accept `--packet-id`, `--private-stage`, `--output`, and `--manifest`, must
   disable hidden retries, and must emit one JSON result for every declared case.
   If the environment has no versioned harness satisfying that interface, record
   `decision=unavailable`; a collection of hand-run commands cannot produce a
   pass.

   With at least two API replicas, the inventory includes: two readers on one
   replica, readers across replicas, retained replay, forced trim and durable
   hydrate, missing Redis key, Pub/Sub restart, attach-time publication, slow
   consumer, authorization-epoch change during a blocked wait, renewal denial,
   API instance loss, callback response loss, and terminal Redis unknown outcome.
   Every case records expected and observed event/cursor, reconnect latency, last
   accepted frame time, lease deadline, terminal state, cleanup result, raw
   artifact reference, and `pass`, `fail`, or `unavailable`.
5. **Run browser and 50-concurrent-Run observations through the same harness.**
   In the frozen browser build, capture a private network trace and screen
   recording for progressive rendering, manual reconnect, gap recovery, final
   hydration, and truthful disconnected state. The load result records its input
   shape, duration, event rate, entry sizes, reconnect and slow-consumer mix, raw
   success counts, first/inter-delta and reconnect p50/p95/p99, Redis
   latency/memory/client counts, PostgreSQL callback/renewal QPS and pool
   occupancy, API/worker memory, and cleanup-to-baseline time. The manifest stores
   the exact harness command and case-inventory digest needed to reproduce it.
6. **Apply stop conditions.** End the run immediately on any frame after its
   recorded lease deadline, privacy leak, cursor regression, unbounded queue or
   memory growth, terminal disagreement, hidden harness retry, subject drift, or
   missing raw evidence. Preserve the failure reason in the packet. Preserve raw
   failed evidence only in the restricted staging directory under the operator's
   retention policy; never upload or attach staging to a PR.
7. **Derive and seal the exportable packet.** An allowlist transform copies only
   the HTTP status line, `Content-Type`, `Cache-Control`, `X-Accel-Buffering`,
   structural event names, cursor prefixes, timestamps, counts, aggregate metrics,
   and digests into `${SSE_ACCEPTANCE_OUT}`. It rejects credentials, cookies,
   prompt/delta content, private paths, environment files, and any unclassified
   field. The operator then runs the repository privacy verifier, records its
   version and result, generates `SHA256SUMS` over the derived packet, makes the
   files read-only, and marks the manifest `pass`, `fail`, or `unavailable`.
   Missing transform/verifier tooling forces `unavailable`. Generate and verify
   the local read-only seal before export:

   ```sh
   (cd "${SSE_ACCEPTANCE_OUT}" && find . -type f ! -name SHA256SUMS -print0 \
     | sort -z | xargs -0 sha256sum >SHA256SUMS)
   (cd "${SSE_ACCEPTANCE_OUT}" && sha256sum --check SHA256SUMS)
   find "${SSE_ACCEPTANCE_OUT}" -type f -exec chmod 0400 {} +
   find "${SSE_ACCEPTANCE_OUT}" -type d -exec chmod 0500 {} +
   ```

   Local permissions are not an immutability boundary. A `pass` additionally
   requires upload to an approved versioned evidence store with object lock or
   equivalent WORM retention. Record the object version, retention deadline, and
   returned digest, then download and re-verify `SHA256SUMS`. If no such store is
   available, the decision is `unavailable`. A `pass` applies only to the exact
   frozen subject and only after a second operator verifies the manifest, privacy
   result, artifact digests, stop-condition result, and locked object version.

The packet manifest contains these required top-level fields:

| Field | Required content |
| --- | --- |
| `packet_id` | unique timestamp-plus-UUID identifier shared by every result |
| `subject` | source SHA, immutable image digests, Nginx fingerprint, protocol/design versions |
| `environment` | observed UTC window, topology, Redis/PostgreSQL/browser versions, security profile |
| `harness` | repository/revision, executable digest, exact command arguments, case-inventory digest, workload shape, stop conditions, retry count |
| `gateway_probe` | response headers, frame timestamps, heartbeat/timeout relation, browser trace refs |
| `fault_matrix` | one record per case with expected/observed cursor, terminal, lease, cleanup, result |
| `load_result` | samples, raw counts, p50/p95/p99, resource peaks, retained replay seconds |
| `privacy_scan` | allowlist-transform revision, verifier revision, rejected fields, result |
| `artifacts` | relative evidence paths and SHA-256 digests |
| `decision` | `pass`, `fail`, or `unavailable`, operator, independent reviewer, reason, reviewed timestamp |

The manifest and every referenced JSON result use canonical UTF-8 JSON with
sorted object keys. Absolute paths and raw-stage paths are forbidden in the
sealed packet. Any undeclared case, missing artifact, digest mismatch, duplicate
packet ID, or non-zero hidden retry count invalidates the decision.

## Evidence states

| State | Meaning |
| --- | --- |
| `local partial` | named focused local checks passed on an exact SHA |
| `PR ready` | Draft candidate and named CI evidence are available for review; not merged/deployed |
| `reviewed` | applicable independent findings are resolved or explicitly accepted under repository policy |
| `External Acceptance pending` | real topology/proxy/browser/load evidence not yet observed |
| `runtime verified` | exact deployed subject passed the separately authorized controlled-host procedure and required runtime checks |

Never promote one state to another without observing the additional evidence.
A pull request or local test does not establish deployment or runtime status.

## External Acceptance record

The runtime evidence packet records exact subjects, commands/harness version,
raw counts and percentiles, Redis clients/memory, PostgreSQL write/query counts,
cursor/gap/terminal cases, revocation commit and lease-deadline timestamps,
proxy headers/config, browser result, privacy scan, cleanup, rollback, failures,
and whether any selector was unavailable. Secrets, raw prompts, delta text, and real
environment files are excluded or redacted.

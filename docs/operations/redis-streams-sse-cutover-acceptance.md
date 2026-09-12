# Redis Streams SSE v4 Cutover and Acceptance

Status: normative release and evidence contract; no deployment is authorized by
this document

Index: [Redis Streams SSE Event Channel](../architecture/redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns implementation grouping, release-atomic cutover,
negative source checks, SSE gateway configuration, focused local/CI gates, and
External Acceptance. The application release procedure remains exclusively owned by
`release-operations-runbook.md`.

## Implementation and review scope

The candidate is one hard cut under [ADR 0013](../adr/0013-redis-stream-only-sse.md):

1. Commit callback facts and receipts, append their exact batch directly to Redis, then acknowledge.
2. Publish committed Run terminal/cancellation facts through the existing lifecycle entry points.
3. Replay and follow the same Redis Stream with bounded XREAD and the existing frontend adapter/reducer.
4. Explicitly retire legacy state before the guarded schema migration.
5. Remove obsolete runtime modules, generated artifacts, selectors, configuration and instructions; validate the complete candidate independently.

Intermediate source or passing slices are not deployment authority. The release
package must satisfy the complete contract and the runbook's immutable-image gates.

## Explicit legacy-state retirement

The Stream-only cutover requires an operator step before schema migration; it
must never run automatically at API or Worker startup. The immutable backend
image includes `/app/tools/retire_legacy_sse_streams.py`.

Use the release runbook's existing Compose project, environment file and image
digests. First finish or cancel old active Runs through the existing Runs
authority, stop the old API/Worker producers, and record a timezone-qualified
cutover timestamp in `CUTOVER_BEFORE`. Run the following with those same Compose
arguments and the new image selected for the `migrate` service. From the
immutable extracted package, with its images already loaded and verified by the
runbook preflight, reuse the exact package Compose identity:

```bash
SSE_COMPOSE=(docker compose --project-name ai-platform-internal \
  --env-file /absolute/path/to/.env -f compose.yaml -f compose.override.yaml)
"${SSE_COMPOSE[@]}" stop api worker
"${SSE_COMPOSE[@]}" run --rm --no-deps --pull never --entrypoint python migrate \
  /app/tools/retire_legacy_sse_streams.py --before "$CUTOVER_BEFORE"
"${SSE_COMPOSE[@]}" run --rm --no-deps --pull never --entrypoint python migrate \
  /app/tools/retire_legacy_sse_streams.py --before "$CUTOVER_BEFORE" --apply
```

Use the same authorized Docker command as `deploy.py` when sudo is required.
Do not omit the package files/project options or create a second Compose project.

The first command inventories only. Apply refuses any selected Run that has not
reached a terminal state. In one transaction it closes old SSE leases, revokes
old stream authorities, supersedes pending terminal intents, aborts incomplete
successor builds, and clears retired publication scheduling from canonical
callback facts. Previously suppressed or inconsistent records remain excluded
from ordinary-user projections. Run/Attempt state, final result bodies, semantic
identities, callback receipts and audit content are retained. Redis keys are not
deleted; old readers are denied by revoked authority and keys expire by TTL.

Keep producers stopped between this operation and the normal release migration.
`applied: true` means commit was acknowledged. A failure before commit reports
`applied: false`; lost commit acknowledgement reports `applied: null` with
`legacy_sse_commit_uncertain`. In that case, inspect the inventory through a new
connection before deciding whether to repeat the operation. A successful repeat
preserves the revocation epoch and prior suppression. Migration refuses any of
the seven publication columns or suppression/scheduling metadata still populated,
and also refuses a partially present legacy column set before any drop. Operators
must resolve that inconsistent layout rather than infer retirement from a missing
marker column. The migration then removes retired tables, columns, indexes and
obsolete index receipts.
This schema change is not compatible with restoring an older backend image;
recovery after migration must use the Stream-only contract.
Do not use this command as proof that the remaining source cutover, tests,
independent review or deployment acceptance has completed.

## Release-atomic rule

A candidate is blocked if any of these remain:

- SDK dispatch before confirmed stream admission;
- callback acknowledgement before its facts commit and its Redis batch appends;
- Redis I/O while the caller holds PostgreSQL transaction locks;
- Run cancellation/terminal or callback publication overtaking an earlier committed counterpart, or terminal publication before Run finalization;
- publication queues/claims, independent terminal intents, Pub/Sub producers/subscribers, pending-admission scans or successor builders;
- missing-stream reconstruction or PostgreSQL browser polling;
- different projection rules for replay and XREAD;
- frontend cursor mutation before reducer/hydrate acceptance, invented IDs, a v3 adapter/fallback, or a second terminal authority;
- generated contract drift or disagreement among source, CI and owning documents;
- dropping unretired state, losing suppressed visibility, modifying business facts or accepting an older binary after migration.

Release preparation binds the exact source SHA, required workflow results,
immutable application image digests and generated v4 artifacts. Local checks
cannot establish those release facts. After schema `2026.09.11.1`, an older
backend is incompatible. Recovery uses a compatible package or the separately
authorized database backup procedure; the deployment entry does not perform
speculative binary/database rollback.

## Retirement inventory and retained consumers

| Surface | Disposition and inspection evidence |
| --- | --- |
| `application/live_fanout.py`, `infrastructure/redis_live.py` | deleted; no Pub/Sub live transport; cutover checker and XREAD/replay tests own absence |
| `infrastructure/postgres_v4.py`, `publication_wakeup.py`, publication claims/drains and pending-admission scans | deleted; direct callback/Run publishers and lifecycle tests inspect every production caller |
| `application/recovery_v4.py`, `infrastructure/redis_v4_rebuild.py` | deleted; missing-stream tests prove no reconstruction or new keys |
| `sse_terminal_publication_intents`, `sse_stream_rebuilds`, `sse_stream_rebuild_items` | explicit retirement then schema-local drop; real PostgreSQL retirement/migration tests |
| seven `run_events.stream_publication_*` columns and their indexes/ledger receipts | guard/clear/drop; real residual-state refusal and obsolete-index cleanup checks |
| `app/streaming/contracts.py` and v3 schema/generator/frontend generated type/adapter | deleted; current API exports and generated-v4/negative-import checks |
| `test_streaming_live.py`, `test_streaming_publication_wakeup.py`, `test_sse_v3_contract_generation.py`, frontend `publicRunStreamV3.test.ts` | deleted; CI/package selectors now cover direct callbacks, XREAD and retirement |
| legacy-named `test_lambchat_sse_v21.py` | existing CI selector retained; its entire active content tests v4 route behavior, not a v2.1 runtime |
| legacy `sev_` terminal-row projection exception and unused old read-count constant | deleted; current Run terminal producer uses `evt4_run_`; stream-open control identities and authorized historical messages keep their existing consumers |
| callback-receipt v2.1 | current executor receipt consumer; independently versioned business protocol, not SSE compatibility |
| opaque Redis `v3` key prefix | retained storage naming only; new runs use the sole v4 runtime; old keys expire without rewriting |
| historical ledger/message decoders and bounded non-streaming final messages | current authorized history/hydration consumers; no live producer or polling fallback; retirement-history and answer-receipt tests preserve visibility and content |
| Run/Attempt, executor reconciliation, cancellation, artifact/file outboxes, final answers and audit | preserved business owners; none is a browser publication queue |

Reviewers inspect or rerun the inventory against the actual diff. A checklist
alone is not absence proof. Historical ADRs retain prior decisions with explicit
supersession; active wire/control/operations instructions follow ADR 0013.

## Negative cutover checker

`python tools/check_sse_runtime_cutover.py --scope full` is a required source
gate. Python AST/import checks and bounded frontend/source checks reject:

- PostgreSQL event/page/fold readers or status/history polling in the live route;
- a second assistant-text ingress or unlisted publication owner;
- Redis access inside callback, Worker or nested transaction helpers;
- SDK dispatch before v4 admission, missing direct publication handoffs, or retired runtime imports;
- Pub/Sub and successor runtime paths, v3 frontend negotiation or generated-contract drift;
- cursor invention/mutation before acceptance or reconnect without `Last-Event-ID`;
- private approval events at the frontend boundary or missing SSE gateway controls.

Bounded per-browser XREAD is required by the current contract. Tests execute the
checker against structural fixtures; source strings alone are not runtime
proof. Required workflow service-backed, frontend and image checks remain
separate evidence owners.

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
within the accepted read-idle budget and carries no event ID. Each request reads
at most 128 entries from the bounded read pool and observes gateway send
deadlines. Cancellation tears down the blocked read. There is no browser event
queue or process-level Pub/Sub feed; process memory is not replay authority.

The existing dedicated SSE location in `frontend/web/nginx.conf.template` owns
these rules and suppresses upstream content encoding. It must not be bypassed by
an earlier regex/preferred prefix, duplicate a conflicting location or weaken
other API routes.

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
- deterministic callback/Redis batch/coalescer/authorization/terminal suites
  through `tools/run_test_stage.py`, with unique workspace-local basetemp/JUnit;
- initial replay, predecessor trim, append-after-tail XREAD, cancellation, pool
  cleanup, missing/expired Stream, terminal race and exact receipt tests;
- committed semantic producer tests proving the strict Skill/tool execution
  projection reaches the Redis reader contract without raw payload fields;
- opt-in real PostgreSQL and real Redis selectors when services are locally
  available, with `--require-zero-skips`; unavailable services are not a pass;
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
  retained history, forced trim, missing key, Redis restart, publication after
  tail capture, foreign/incarnation cursor rejection, gap and durable hydrate;
- Redis unavailable at admission proving zero SDK dispatch; mid-run outage for
  eligible completion, bounded memory, no PostgreSQL delta fallback, and
  truthful terminal convergence;
- callback HTTP response loss, duplicate batch conflict, Redis `XADD` unknown
  outcomes, PG terminal rollback, PG commit plus terminal Redis unknown outcome,
  callback/terminal commit-to-append races, partial terminal/end retry, unchanged
  business facts and exact terminal payload digest;
- authorization-epoch commits across API replicas, blocked live wait, slow
  downstream delivery, instance restart/loss, renewal denial, and no old-epoch
  application/gateway frame after the recorded <=15-second lease deadline;
- Nginx buffering/cache/compression disabled, heartbeat beneath timeout, bounded
  slow client behavior, gateway close on lease expiry, and browser-observed
  progressive rendering/final replacement;
- ordinary-user privacy scan proving no raw command/tool payload, hidden
  reasoning, credentials, paths, or storage keys;
- connection/pool cleanup to baseline and compatible-package failure recovery.

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
   hydrate, missing Redis key, Redis restart, publication after tail capture, slow
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

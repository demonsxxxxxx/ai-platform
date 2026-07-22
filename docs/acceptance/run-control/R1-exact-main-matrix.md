# R1 exact-main Run Control acceptance matrix

## Scope and safety boundary

This matrix is the R1 acceptance harness for #544 / #512 Run Control.  It
exercises the deployed HTTP API only; it does not invoke an executor SDK,
start a local product runtime, create a sandbox, or replace the platform queue.

The operator supplies already-created, disposable same-tenant fixture runs.  The
script mutates the queued, running, retry-source, and resume-source fixtures.
It refuses to send those requests without `--allow-mutations`.  It never creates
or deletes fixture data, so fixture provisioning and cleanup remain operational
responsibilities outside this R1 change.

`--branch main`, `--commit-sha`, and `--runtime-subject-commit-sha` make the
intended runtime subject explicit.  The verifier requires the two commit values
to match, but cannot independently authenticate an image label from the public
Run Control API; deployment evidence must supply that independent proof.

## Required fixture state

All IDs must be safe existing IDs in the selected tenant and belong to the
owner principal, except where noted.

| Input | Required starting state | Mutation |
| --- | --- | --- |
| `--queued-run-id` | queued and not yet executed | owner cancel |
| `--running-run-id` | running with a platform-verified sandbox lease | owner cancel |
| `--retry-source-run-id` | failed or dead-lettered retryable run | retry twice with one operation ID |
| `--resume-source-run-id` | terminal run with reusable checkpoint output | resume twice with one operation ID |
| `--stale-session-run-id` | owner run whose session is no longer active | read-only denial probe |

The stale-principal probe uses the retry source with `--stale-principal-user-id`.
It must be a different user in the same tenant.

## Cases

| Case ID | Contract checked | Evidence scope |
| --- | --- | --- |
| `queued_cancel_no_execution` | queued cancel reaches `cancelled`, no step remains `running`, and the terminal run no longer has queue position | runtime HTTP |
| `running_cancel_fence_and_sandbox_cleanup` | running cancel records a cancellation fence, no start event follows it, and admin detail has no active sandbox lease | runtime HTTP |
| `retry_idempotency_and_lineage` | repeated retry using one UUID operation resolves to one child, queue admission is admitted, and child event lineage points to the source | runtime HTTP |
| `resume_eligibility_and_lineage` | readiness enables resume, repeated resume resolves to one child, and the child resume manifest retains the source/reuse intent | runtime HTTP |
| `sse_duplicate_order_replay` | terminal queued-cancel events replay through SSE with unique IDs and monotonically increasing sequences | runtime HTTP |
| `refresh_terminal_hydration` | two refreshes consistently hydrate terminal status, cancellation metadata, and terminal readiness | runtime HTTP |
| `stale_principal_and_session_denial` | a different principal and an inactive-session run each return the owner-scoped `404 run_not_found` response | runtime HTTP |

## Evidence labels

| Label | Meaning for this R1 change |
| --- | --- |
| `local` | deterministic verifier tests and Python compile checks; no external API is contacted |
| `integration` | not executed locally: the matrix needs pre-provisioned persistent queue/database fixtures |
| `runtime_http` | produced only when `verify_exact_main_run_control.py` targets an operator-provided runtime |
| `runtime_browser` | not applicable and not run: this is a backend API acceptance harness and R1 does not open a browser |

## Invocation

Run only on the intended deployed environment after fixture provisioning and
separate runtime-source identity verification:

```powershell
$env:AI_PLATFORM_GATEWAY_SECRET = '<operator-supplied secret>'
python tools/acceptance/run_control/verify_exact_main_run_control.py `
  --base-url https://ai-platform.example `
  --commit-sha <deployed-main-commit> `
  --runtime-subject-commit-sha <deployed-main-commit> `
  --tenant-id <tenant> `
  --owner-user-id <fixture-owner> `
  --admin-user-id <fixture-admin> `
  --stale-principal-user-id <different-user> `
  --queued-run-id <queued-fixture> `
  --running-run-id <running-fixture> `
  --retry-source-run-id <retryable-fixture> `
  --resume-source-run-id <checkpoint-fixture> `
  --stale-session-run-id <inactive-session-fixture> `
  --allow-mutations
```

The JSON output deliberately records redacted status and boolean observations,
not raw request headers, secrets, input, output, artifact, or sandbox payloads.
`ok: true` means all seven HTTP cases passed against the supplied runtime; it is
not a deployment, image-label, browser, or cleanup attestation.

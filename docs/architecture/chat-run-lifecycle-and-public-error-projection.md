# Chat Run Lifecycle And Public Error Projection

Status: current public-projection contract and implementation map. Historical
queue/error repair narratives live in their original Git revisions and PRs.
This page does not define another Run, queue, Sandbox or SSE authority.

## Owners and protocol names

The public stream is SSE v4, defined by [SSE wire](redis-streams-sse-wire-protocol.md).
The internal callback receipt protocol is independently v2.1. Legacy internal
handler names such as `stream_open` and `final_detail` are projection details;
they do not enable a v3 browser adapter or retired `queue_update` producer.

Runs owns execution state and fixed safe terminal classification. The existing
`app/run_projection.py` compatibility surface delegates to the owning projection;
its eventual source migration must not create a second taxonomy.
`publicTerminalPresentation.ts` owns frontend presentation of approved public
codes. Live, historical and hydrated messages use the same presentation catalog
and message projection semantics.

## Queue-to-processing presentation

A truthful submission/status response may show `queued` and `queue_position`.
An accepted current-Run v4 `stream.open` clears the queue presentation after
schema, binding, incarnation, connection-generation and cursor validation.
Opening a stream proves publisher readiness, not successful Tool execution or
available global capacity. An internal mapping to `stream_open` is allowed.
A stale frame cannot dismiss the current Run's queue state. Terminal, error,
cancellation, session replacement and setup failure retain idempotent cleanup.

Queue metrics, scheduling limits and Redis facts remain supported. Do not
reintroduce the retired browser `queue_update` fallback. A disconnected browser
does not determine the Run's execution state.

## Terminal and content facts

Run outcome, transport end and final-content synchronization are distinct.
Keep accepted safe partial text while a terminal status is displayed. Final
hydration replaces the provisional fold; it does not append the answer again.

`result_unavailable` is backend-confirmed absence of a displayable terminal
answer. `terminal_result_unavailable` is a frontend condition where a known
terminal result could not be synchronized. `status_unavailable` denotes missing
status evidence and cannot replace a known terminal failure. None authorizes
automatic resubmission of the user's task.

Only the backend-approved fixed code/kind/severity taxonomy selects public text.
Unknown, private, malformed or kind-mismatched details use the fixed `run_failed`
fallback. Frontend display ignores arbitrary backend message text for these
status cards. Distinguish execution-service unavailability from explicit model
upstream failure according to the owning code mapping.

Public projection failures keep the first allowlisted reason and the fixed
`claude_agent_sdk_public_projection_failed` category. The optional reason is
public only for a failed Run with that exact category and an allowed value.
Missing historical reasons remain unknown. This preserves the failure contract
also recorded in the historical SDK upgrade note.

## Private diagnostics and reconciliation

Ordinary-user routes, SSE, history and status cards must never render raw SDK,
parser, Tool or provider exceptions, inputs/results, credentials, paths, storage
keys or private execution identities. Admin diagnostics remain a separately
authorized bounded projection, never an alternate public endpoint.

The retained diagnostic boundary limits structured SDK/Tool values to 4 KiB,
exception text to 8 KiB, lightweight lifecycle facts to 128, detailed calls and
policy denials to the latest eight, and the aggregate block to 128 KiB within
the 256 KiB Run result bound. Preserve newest useful evidence and normalize at
each producer/trust boundary. An admin role is not permission to expose secrets.

Terminal-reconciliation failure uses the existing fixed code
`terminal_reconciliation_failed` for the owning permanent-contract failure or
exhausted retry policy. Private reasons remain private. Existing source budgets
include a 240-second work deadline under a 300-second stale-claim interval and a
narrower stop timeout; the current retry policy treats the fifth unclassified
terminal failure as terminal failure. These are implementation budgets, not
end-user latency guarantees. Before changing them, verify the exact code and
matching tests and record effective deployed configuration separately.

Claim the exact eligible receipt in a short transaction. Re-prove tenant,
workspace, user, Session, Run and Attempt before restoring context or collecting
outputs. Unverified historical handles remain quarantined. Verified failed-stop
handles remain recoverable. Later mutations compare the exact claim token.
A historical released lease with an eligible non-finalized receipt may still
need reconciliation; generic cleanup cannot bypass this receipt fence.
Release/finalize only after the owned stop outcome is proven. Do not synthesize
an artifact count of zero or discard independently authorized artifact records
when finalization fails.

Admin health exposes scoped counts, oldest pending age and SLO-breach counts;
these are observations, not identifiers or private errors. A reported historical
15-minute breach threshold is not proof that current reconciliation meets a
15-minute service objective. Runtime acceptance must measure it independently.

## Implementation and acceptance

`eventProcessor.ts`, `historyLoader.ts`, terminal hydration and
`MessagePartRenderer.tsx` must use the shared code catalog. Validate every
shipped locale/default against the backend code/kind/severity contract. Do not
invent an unshipped locale. Keep tests for unknown/malicious message text,
partial-content preservation, all approved error families, encrypted-file
password guidance, queue ownership and hydrated failure presentation.

[System acceptance](../acceptance/system-architecture-matrix.md) adds recovery,
client-state and fault-isolation scenarios. Source and local tests do not prove
SDK timing, API replicas, proxy flushes, deployed browser behavior or provider
cleanup. Those remain under [SSE acceptance](../operations/redis-streams-sse-cutover-acceptance.md)
and the release runbook. No source cleanup authorizes a weaker public projection.

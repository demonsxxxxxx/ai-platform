# R1 Run Control acceptance evidence boundary

## Purpose

R1 records the local, focused contract coverage for #544 / #512 and defines the
evidence packet expected from a later runtime/browser acceptance owner.  It does
not run an environment, create fixtures, mutate a run, or claim runtime
acceptance.

The only R1 helper is the pure-offline
`tools/acceptance/run_control/validate_run_control_evidence_packet.py`.  It reads
at most 64 KiB from one explicitly supplied local JSON packet.  A linear,
string/escape-aware resource guard rejects raw nesting deeper than 12 before
semantic decoding; bounded inputs then use `json.loads` with duplicate JSON
object-member rejection at every nesting level before schema or secret
validation.  A duplicate member is not promised to outrank an over-depth input:
the resource guard runs first.  It has no network, credential, environment,
process, write, product-state, or evidence-reference probing behavior.  Its
output is only `schema_valid` or `schema_invalid`; **schema validity is not
runtime proof** and cannot generate, upgrade, or prove evidence.  Other bounded
decode failures, including a configured JSON-integer digit limit, use a fixed
redacted invalid-JSON diagnostic without exception detail.

## Authoritative local test nodes

Run local, focused evidence only:

```powershell
python -m pytest tests/integration/run_control/test_exact_main_run_control_acceptance.py -q --basetemp .pytest-tmp\run-r1-evidence-packet
python -m pytest tests/test_run_control_routes.py::test_retry_operation_replays_resolve_the_same_child_without_duplicate_creation tests/test_run_control_routes.py::test_resume_run_creates_queued_resume_from_checkpointed_source tests/test_run_control_routes.py::test_cancel_queued_run_removes_queued_payload tests/test_run_control_routes.py::test_cancel_run_stops_active_sandbox_runtime_before_db_release tests/test_routes.py::test_run_event_stream_emits_existing_events tests/test_routes.py::test_deleted_session_run_reads_deny_before_child_resource_reads -q --basetemp .pytest-tmp\run-r1-existing-contracts
python -m py_compile tools/acceptance/run_control/validate_run_control_evidence_packet.py tests/integration/run_control/test_exact_main_run_control_acceptance.py
```

These nodes are local source evidence.  They do not replace runtime or browser
evidence, and their fixtures must not be reported as deployed proof.

## Exact-main provenance packet

The validator accepts only this redacted shape:

```json
{
  "schema_version": "ai-platform.run-control-r1-evidence.v1",
  "source": {
    "branch": "main",
    "commit_sha": "<40-character SHA>",
    "runtime_subject_commit_sha": "<same 40-character SHA>"
  },
  "cases": [
    {
      "case_id": "runtime_run_control",
      "status": "evidence_recorded",
      "evidence_refs": [
        {"type": "source", "ref": "evidence/source-main.json"},
        {"type": "runtime", "ref": "evidence/runtime-run-control.json"},
        {"type": "browser", "ref": "evidence/browser-ordinary-user.json"}
      ]
    },
    {
      "case_id": "browser_ordinary_user_run_control",
      "status": "evidence_recorded",
      "evidence_refs": [
        {"type": "source", "ref": "evidence/source-main.json"},
        {"type": "runtime", "ref": "evidence/runtime-run-control.json"},
        {"type": "browser", "ref": "evidence/browser-ordinary-user.json"}
      ]
    }
  ]
}
```

Each recorded claim needs distinct `source`, `runtime`, and `browser` labels.
Every label must use exactly one canonical type-matching relative name:

| Type | Only accepted reference grammar |
| --- | --- |
| `source` | `^evidence/source-[a-z0-9][a-z0-9-]{0,63}\.json$` |
| `runtime` | `^evidence/runtime-[a-z0-9][a-z0-9-]{0,63}\.json$` |
| `browser` | `^evidence/browser-[a-z0-9][a-z0-9-]{0,63}\.json$` |

The validator rejects duplicate or missing cases, malformed SHA/status, duplicate
JSON members, secret-like material, and noncanonical references (including URLs,
absolute paths, traversal, backslashes, extra directories/dots, underscores, and
JWT/PAT-shaped values).  It validates only packet shape, cross-fields, type
labels, and canonical names.  It does **not** establish a reference's existence,
provenance, freshness, runtime/browser origin, principal, or deployed behavior;
the presence of `runtime` or `browser` labels is not evidence of either.

## Deferred runtime and browser owner scope

After deploy, exactly one project-bound runtime/browser acceptance owner must
own fixture provisioning, mutations, observation, and cleanup.  This R1 task is
not that owner and must not perform any of those actions.

| Evidence case | Runtime/browser owner must observe |
| --- | --- |
| `runtime_run_control` | queued cancel before execution; running cancel fence and sandbox cleanup; retry idempotency and lineage; resume eligibility and lineage; SSE duplicate/order replay; terminal refresh hydration; stale principal and inactive-session denial |
| `browser_ordinary_user_run_control` | ordinary-user same-tenant controls and projections; no cross-user disclosure; refresh/reconnect terminal state; no raw runtime paths, credentials, or private payloads in visible UI |

The runtime/browser owner creates disposable same-tenant fixtures, records their
provenance and exact deployed-main identity, and cleans them using the approved
runbook.  It must separate current runtime/browser observations from historical
or local evidence.  R1 provides neither credentials nor fixture IDs and must
not perform cleanup.

# R1 Run Control acceptance evidence boundary

## Purpose

R1 records the local, focused contract coverage for #544 / #512 and defines the
evidence packet expected from a later runtime/browser acceptance owner.  It does
not run an environment, create fixtures, mutate a run, or claim runtime
acceptance.

The only R1 helper is the pure-offline
`tools/acceptance/run_control/validate_run_control_evidence_packet.py`.  It reads
one local JSON file and validates a narrow, redacted schema against an explicitly
supplied 40-character `main` SHA.  It has no network, credential, environment,
process, write, or product-state behavior.  Its output is only `schema_valid` or
`schema_invalid`; **schema validity is not runtime proof** and cannot generate,
upgrade, or prove evidence.

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

Each recorded claim needs distinct `source`, `runtime`, and `browser` reference
types.  The validator rejects missing or duplicate cases, malformed SHA/status
or references, mismatched subject SHA, secret-like material, and local-only
evidence presented as a runtime/browser claim.

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

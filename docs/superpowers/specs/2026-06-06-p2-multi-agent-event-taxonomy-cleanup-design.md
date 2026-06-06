# P2 Multi-Agent Event Taxonomy Cleanup Design

## Goal

Close the remaining non-blocking P2 multi-agent dispatch event taxonomy follow-up
without changing persisted history, opening new sandbox/tool privileges, or
adding a new frontend entry.

## Current Evidence

- The PRD requires typed event, playback, provenance, checkpoint, subagent, and
  artifact tree contracts before broad Long Task / Multi-Agent Runtime use.
- The foundation roadmap records P1 Admin Runtime, Memory / Context, and Tool
  Permission / Agent Frontend V1 as deployed and smoked on 211.
- The roadmap records deployed P2 slices through Multi-Agent Worker Dispatcher
  and names one remaining non-blocking follow-up: older multi-agent dispatch
  event taxonomy cleanup.
- Current local and 211 code include `multi_agent_parent_finalized` in
  `STANDARD_EVENT_TYPES`, but not these persisted runtime event types:
  `multi_agent_dispatch_handoff`, `run_multi_agent_child_created`,
  `multi_agent_dispatch_enqueue_failed`, `multi_agent_dispatch_reconciled`,
  and `multi_agent_dispatch_parent_parked`.
- 211 health is `ok`; API and worker image labels and source markers are at
  `92bef5c6e196bcbe4bc563e3ad50d1d96a629d7d`; recent API/worker logs showed no
  error markers during pre-slice inspection.

## Recommended Approach

Keep persisted event names backward-compatible and add them to the standard
event taxonomy. For ordinary-user projections, map the public child-run creation
event away from the internal multi-agent dispatch name. Hidden dispatch control
events remain hidden and keep their internal names for admin/operator use.

This avoids a DB migration and preserves historical playback while making the
event contract explicit for future observability, quality, and long-task
runtime consumers.

## Alternatives Considered

1. Only add missing names to `STANDARD_EVENT_TYPES`.
   This is smallest, but ordinary users can still see
   `run_multi_agent_child_created`, which is an internal runtime name.

2. Rename persisted event writes to new canonical names.
   This creates split history and requires compatibility aliases or a migration.
   It is unnecessary for this cleanup.

3. Recommended: keep storage stable, expand taxonomy, and add public projection
   aliasing for the visible child-run event.
   This closes the contract gap with low blast radius.

## Contract

`STANDARD_EVENT_TYPES` must include:

- `multi_agent_dispatch_handoff`
- `run_multi_agent_child_created`
- `multi_agent_dispatch_enqueue_failed`
- `multi_agent_dispatch_reconciled`
- `multi_agent_dispatch_parent_parked`
- existing `multi_agent_parent_finalized`

Ordinary-user projection must map:

- `run_multi_agent_child_created` -> `run_child_created`

The mapped payload remains public-safe and must not expose dispatch ids,
parent run ids, parent step ids, storage keys, private payloads, runtime paths,
command fingerprints, or secret-like values.

Ordinary-user projection redaction must remove both `copied_from_run_id` and
`parent_run_id` forms of server-owned parent linkage.

Admin projections keep raw event names because they are operational taxonomy and
same-tenant/admin-only visibility still applies.

## Non-Goals

- No DB migration.
- No persisted event rename.
- No new public route.
- No frontend entry.
- No sandbox provider change.
- No tool permission or write-tool policy change.
- No exposure of hidden dispatch control events to ordinary users.

## Testing

- Add RED contract coverage in `tests/test_control_plane_contracts.py` for all
  multi-agent dispatch event names.
- Add RED projection coverage in `tests/test_routes.py` proving ordinary users
  see `run_child_created` while admin users keep `run_multi_agent_child_created`.
- Add RED projection coverage for root-level `parent_run_id` leakage in visible
  child-created payloads.
- Keep existing dispatch/handoff/reconcile tests unchanged unless the new
  assertions require explicit compatibility coverage.

## 211 Smoke

After local verification and review, deploy the runtime source to 211 and prove:

- API health is `ok`.
- API/worker image labels and source markers match the pushed revision.
- In-container taxonomy reports all dispatch event names as standard.
- In-container `run_event_response` maps the child-created event for an ordinary
  principal and keeps the admin name for an admin principal.
- Recent API/worker logs contain no new error markers.

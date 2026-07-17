# Issue #487: Context v1 prompt continuity design

## Scope and authority

This change makes the existing database-backed executor snapshot and its
context-pack the sole product authority for same-session continuity.  It does
not add a transcript store, Redis state, a migration, a public-schema change,
or cross-session/Fork consumption.  The current run remains the capability
anchor: all message, file, and artifact references stay constrained by the
snapshot's tenant, workspace, user, session, and run scope.

## Prompt contract

At snapshot creation, the scoped database session tail is ordered
chronologically and the current-run message is excluded from the manifest's
prior-message material.  The executor context pack carries only the bounded
prior messages selected from that snapshot.  On the first SDK prompt, those
messages are rendered in a role-delimited, explicitly untrusted section.  The
current request is rendered once from the run input; it is never rendered from
the prior-message section.

Files and artifacts remain reference-only.  Their names, IDs, and the
retrieval-tool inventory may be prompt metadata, but their contents and object
locators are not inserted into the prompt.  Advertised Context tools are the
same subset that has both a non-empty manifest reference and an authorized
broker subject.

## Bounds and safety

One UTF-8-byte-based estimator is used wherever Context v1 applies a token
budget.  It is intentionally conservative for ASCII, CJK, and emoji.  The
planner applies independent per-message and total-history byte caps before
material enters the pack; prompt rendering independently caps the current
request, file-name list, summary/metadata, and history section.  Truncation is
UTF-8 safe.  Reserved internal subjects, public projections, callback fencing,
and retrieval-only file/artifact behavior are unchanged.

## SDK continuity

Each run deterministically derives a distinct SDK session ID from its run ID.
The value is stateless, so a different worker can reconstruct it, but it never
forms a cross-run transcript resume key.  The worker no longer maintains
in-process transcript state or a session lock.  Correctness therefore depends
on the stored snapshot/context pack, not implicit SDK persistence.

## Verification

Focused tests cover role/order/current-message de-duplication; UTF-8 CJK,
ASCII, and emoji bounds; retrieval-subject alignment; run-scoped session IDs
and worker reconstruction.  The local gate is focused pytest with a workspace
base temp, `python -m compileall -q app tools scripts`, and `git diff --check`.
This design does not claim runtime, deployment, or browser evidence.

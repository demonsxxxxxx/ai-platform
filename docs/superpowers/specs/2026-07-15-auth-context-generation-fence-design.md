# Auth-context generation fence (Issue #448)

## Status and scope

This is the approved security design for the no-Web-Locks browser path.  It
extends the browser-auth bootstrap protocol without changing relational
schemas, deployment configuration, or the trusted-header and Bearer
compatibility paths.  The existing Web Locks plus localStorage V1 path remains
unchanged.

The threat boundary is **authorization safety**, not confidentiality of a
plain HTTP transport.  A delayed, previously-authorized HTTP response can
still physically apply an old `Set-Cookie`.  It may cause finite repair work or
a transient denial of service, but it must never restore an old user, tenant,
role, permission, session, or auth operation authority.  Sustained active
network denial of service and confidentiality require trusted TLS.

## Protocol state

V2 is used only when Web Locks are unavailable and IndexedDB is usable.  The
browser creates one 256-bit base64url `incarnation` in a readwrite IndexedDB
transaction.  Its persistent record is:

```text
{ incarnation, currentGeneration, currentNonce,
  pendingRotation?: { baseGeneration, nextNonce, ticket },
  ownerToken, leaseExpiresAt }
```

The same-name HttpOnly cookie has this strict canonical form:

```text
v2.<base64url(canonical JSON {"i": incarnation, "g": generation,
                              "h": contextHandle})>.<base64url(HMAC-SHA256)>
```

The MAC is calculated with `auth_context_secret` and a protocol-specific
label.  The server rejects malformed version, alphabet, lengths, generation,
canonical payload, and MAC before Redis access.  The cookie continues to use
the configured HttpOnly, SameSite, Secure, Path, and maximum-age properties.

`POST /api/ai/auth/bootstrap` keeps the V1 `{nonce}` request.  V2 adds
`protocol_version: 2`, `browser_incarnation`, `generation`, and only during a
server-authorized rotation, `rotation_ticket`.  V2 responses add protocol and
generation metadata.  Cookie emission is driven solely by a structured result
from the atomic store operation: new V2 context, V1 matching-context migration,
exact current-owner repair, or successful rotation.  A stale, mismatch,
invalid-ticket, missing, corrupt, or Redis-failure result never sets or clears
a cookie.  An already-at-target rotation reconciliation returns `ready` but
does not refresh `Set-Cookie`, because the signed target cookie is the proof
that the original response reached the browser. A separate base-cookie target
repair can return `ready` with only the target cookie when the rotation committed
server-side but its response headers never reached the browser.

## Redis authority and Lua/CAS seams

The existing context key remains:

```text
ai-platform:auth-context:<context handle>
```

The V2 authority key is:

```text
ai-platform:auth-browser-authority:<HMAC(auth_context_secret, incarnation)>
```

The authority record contains schema version, incarnation digest, generation,
context handle, and optionally a digest-only rotation ticket, its base
generation, and deadline.  These three ticket fields are all absent or all
present; when present, the base generation equals the authority generation.
A V2 context record contains `protocol_version`, the same incarnation digest,
and generation.  No ticket, nonce, incarnation, cookie value, or secret is
logged.

The following operations are single Lua transactions:

1. V2 create, matching V1 migration, dedupe, and repair over authority/context
   keys.  A V1 migration copies the context's remaining `PTTL` to authority;
   it never extends an existing session.
2. Exact `g -> g + 1` rotation over the authority, old context, and new
   context.  It validates and consumes one server-issued ticket atomically,
   and carries the old authority/context's remaining `PTTL` to both successor
   records. Reissuing a ticket may replace it only while the authority is
   unchanged.
3. Exact-target reconciliation over authority and the target context. It can
   acknowledge only a signed target cookie whose `(i, g + 1, h-next)` is
   already equal to both records; it never consumes/reissues a ticket or
   changes a TTL.
4. Base-cookie target repair over authority, old context, and target context.
   It accepts only a signed `(i, base, h-old)` cookie and exact
   `(i, base + 1, h-next)` authority/target records, then reissues only the
   target cookie with remaining `PTTL`. It never mutates Redis or consumes a
   ticket. If—and only if—authority is still exact base with no target context,
   it returns the typed signal permitting one normal base ticket reissue.
5. V2 principal snapshots over authority and context.  There is no Python
   get-then-get validation.
6. Begin and commit auth operations, OAuth state issue/consume, OAuth callback,
   and logout.  The operation and OAuth state carry the V2 identity, so a
   generation transition supersedes old work in the same atomic operation.

New V2 state receives the configured maximum TTL; matching V1 migration and
V2 rotation carry the old context's remaining `PTTL`; repair and dedupe never
refresh it. The existing auth-operation lease duration is retained. Missing
authority, context/authority disagreement, invalid state, TTL disagreement,
and Redis loss all fail closed.

## Authorization invariant and recovery

A V2 browser cookie is accepted only if its verified identity is exactly equal
to both the Redis authority record and the V2 fields of the context record.
Every context-authorized route checks this invariant atomically.  Therefore,
after authority moves from `(i, g, h)` to `(i, g + 1, h2)`, an old response
that installs `(i, g, h)` cannot obtain a principal or start/commit a login,
logout, or OAuth operation.

The current IndexedDB owner retains the current incarnation, generation, and
nonce.  On a typed stale response it performs one exact V2 bootstrap repair and
an idempotent principal GET may retry once.  Login, logout, OAuth, uploads,
Chat, and all other mutation POSTs are never replayed automatically.  IDB
unavailable, corrupt, blocked, timed out, or cancelled before acquisition
fails before bootstrap with the localized safe-coordination UI.

If a rotation response succeeds server-side but local IDB promotion is aborted,
expired, or versionchanged, the next owner retries the persisted pending target.
A signed target cookie can reconcile without a cookie write. If fetch aborted or
the response was lost before `Set-Cookie`, the signed cookie remains base: after
one stale/unknown ticketed result, the owner makes exactly one no-ticket target
repair. The server must prove old signed `(i, base, h-old)` plus authority and
target context `(i, base + 1, h-next)` before it reissues the target cookie and
the owner promotes IDB without clearing browser storage. If the server instead
proves authority is still exact base and no target exists, the owner may make
one normal base ticket reissue for the same nonce, then one final ticketed
rotation. Any other state, a changed owner/base/pending nonce, or a second
recovery branch fails closed; no recovery loop or business-mutation replay is
allowed.

Lease expiry does not let an old owner publish, rotate, or release a newer
record.  Each IndexedDB transaction accepts a signal/deadline, is aborted for
cancellation, rereads ownership after every await, and validates ownership and
generation before network mutation or publication.  A late database-open
success closes immediately after timeout/abort. A settled blocked/timeout/error
open retains a late `upgradeneeded` handler solely to abort the upgrade
transaction before it can mutate schema. Each coordinator database session
tracks its own live readwrite transactions and explicitly aborts them on
`versionchange` before closing; all handlers are then cleaned up.

## Migration and rollback

Unmigrated V1 contexts and Web Locks clients remain valid.  A V1 context is
migrated only when the imported legacy localStorage nonce derives exactly the
authenticated V1 context handle.  An authenticated nonmatching V1 context is
never overwritten; ambiguous migration fails closed.  After migration, a raw
V1 cookie for that context is rejected on protected routes, preventing V2 to
V1 downgrade. A V1 bootstrap request carrying any `v2.` cookie is rejected
before V1 Lua or `Set-Cookie`, because V1 supplies insufficient incarnation and
generation proof. The V2 bootstrap repair path can replace that physically
stale raw V1 cookie only after proving current V2 authority from persisted
state.

Deploy server V1/V2 parsing and validation before enabling the no-Web-Locks
V2 client.  A rollback may stop issuing new V2 contexts, but must retain V2
validation for at least the maximum context TTL (or explicitly invalidate V2
sessions).  It must not rewrite V2 cookies as V1.

## Failure modes

- Invalid MAC/format, stale or gap generation, conflicting context, invalid or
  expired ticket, malformed/partial ticket tuple, malformed Redis state,
  authority/context mismatch, Redis loss, and V2 context TTL loss: fail closed
  without `Set-Cookie`.
- A target repair rejects wrong incarnation, old or next handle, target
  generation gap, missing/corrupt target, and any authority/target TTL mismatch.
- Same nonce/context is deduplicated atomically.
- Two same-profile no-cookie tabs share the one IndexedDB state and bootstrap
  identity; independent browser profiles have distinct, unauthenticated
  contexts.
- A late stale cookie can cause one bounded repair or a transient denial of
  service, never authorization rollback.

## TDD and verification matrix

Backend tests cover strict parser/MAC checks, V1 compatibility and migration,
same-nonce dedupe, context/generation conflicts, target-cookie reconciliation,
base-cookie committed-target repair, ticket single-use/reissue/late-response
ordering/expiry, TTL preservation,
partial ticket tuples, Redis loss/corruption, stale-cookie reversed arrival,
principal/login/logout/OAuth/commit fencing, and different-user operation
races. Frontend tests use an asynchronous IDB/transaction/cookie-jar double
for shared tabs, rollback/abort, blocked-to-late-upgrade/success, explicit
versionchange transaction abort, lease expiry, target repair after a lost
rotation response, bounded base ticket reissue, rotation, and stale-repair
semantics. The double is not proof of the real
HTTP-IP browser race or actual Redis execution.

Focused gates are auth session/routes/principal pytest; coordinator/provider/
auth API tests; company RBAC browser source smoke; scoped lint; TypeScript;
projection audit; production build; compileall; diff check; scope and secret
scans; and the repository large-feature checklist.  A fresh independent
security review and controller-owned runtime acceptance remain required.

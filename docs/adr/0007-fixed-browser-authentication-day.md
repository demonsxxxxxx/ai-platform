---
status: accepted
---

# Fix browser authentication lifetime at 24 hours

Design ID: `ai-platform.browser-authentication-day.v1`

Accepted on 2026-08-13 for the authentication contract tracked by #1004.

## Context

Browser authentication previously had three independently configurable clocks:
the signed session token, the Redis authentication context, and the freshness
window for company authority. Their defaults differed, so a valid browser token
could outlive the server-side authority snapshot and force an unexpected login
after 15 minutes.

## Decision

1. The platform session-token lifetime (`ai_session_max_age_seconds`), Redis
   context lifetime (`auth_context_max_age_seconds`), and company-authority
   freshness window (`company_authority_freshness_seconds`) are fixed at exactly
   86,400 seconds in source and Compose, not operator-configurable. These platform
   clocks do not extend the validity of an independently issued company JWT.
2. The lifetime is absolute and non-sliding. Authenticated reads, profile
   updates, and token rotation preserve the remaining server-side TTL rather
   than extending it.
3. A token whose expiration is equal to the current time is expired.
4. Within that absolute day, an expired company JWT may be renewed after a
   current company-authority check. An authentication rejection is replayed
   at most once; writes require proof that business execution never started.
   When the 24-hour context expires, the user authenticates again.
5. Company-derived roles and permissions already captured by a valid session
   may remain effective for up to 24 hours. Immediate revocation requires a
   separate explicit revocation mechanism and is outside this decision.

## Consequences

- A successful login remains usable for one predictable day without the former
  15-minute authority mismatch.
- Stale lifetime values in a managed `.env` cannot split API and worker
  behavior because Compose supplies the fixed contract directly.
- Sessions cannot become indefinitely renewable through ordinary activity.
- Company account, role, or permission revocation may take up to 24 hours to
  reach an already authenticated browser session.

## Rejected alternatives

- A 24-hour token with a shorter company-authority window: this recreates the
  unexpected reauthentication defect.
- Sliding Redis or token renewal: active sessions could remain valid without a
  bounded authentication ceremony.
- Unbounded refresh or replay of ambiguous writes: these can extend authority
  indefinitely or duplicate business effects.

## Browser refresh extension

The browser may recover an expired company JWT within its existing absolute
86,400-second authentication context. The JWT lifetime is independently bounded
at 28,800 seconds and never exceeds the remaining context lifetime. Expired
JWTs remain invalid; renewal issues a newly signed company-compatible JWT.

The refresh endpoint authenticates the signed HttpOnly context and its current
server-side principal, not the expired JWT or browser-supplied identity fields.
It rechecks company user eligibility and permissions before signing. Anonymous,
logged-out, replaced, policy-incompatible, or expired contexts cannot renew.
Company username, department, and original role are server-owned snapshot fields;
platform `roles` remain the platform permission projection.

A refresh commits the principal and encrypted MCP credential under the existing
context operation fence. Credential-only rotation preserves the principal's
subject epoch; changed authorization facts invalidate old authority owners.
Successful credential-bearing refresh commits record their operation epoch for
concurrent request deduplication, including when JWT expiry is already capped at
the context deadline. This optional context-local field defaults to zero for old
records and expires with the context. Login/logout attempts clear it; failed
refresh cleanup never creates completion evidence. Only the same subject epoch
may reuse completion; changed authorization is rechecked or fenced.
Neither a browser request nor an MCP request extends the context deadline.
Grant snapshots and operation leases derive their returned `expires_at` from the
earlier absolute Redis expiry of the authority and context keys, converting
milliseconds to seconds once. The projection does not rewrite either key.
MCP credential reads and request dispatch obtain the current credential under
the same bounded grant, including for an already-open selected-tool session.

Browser requests share a single refresh, retry an authentication rejection at
most once, and fence cancellation, logout, and identity replacement. Login,
logout, bootstrap, and refresh requests cannot recursively refresh. Only a
rejection before business execution permits automatic replay; ambiguous writes,
transport failures, and already-started MCP tool effects are never replayed.
Network or service failures preserve the local session presentation; exhausted
or revoked refresh authority enters the existing login recovery path.

### Automatic company reauthentication after context expiry

When an authenticated browser request reports a missing or stale platform
context, the AuthProvider may perform one new Windows AD authentication before
returning the expiry failure. It reads the configured `GetADName` endpoint for
a fresh company JWT, runs the existing browser-context bootstrap/recovery, and
submits that JWT to the platform AD login endpoint. The server verifies the
new credential and establishes a new authentication day; this is a new login
ceremony, not an extension of the expired grant. A revoked company authority
or a permission denial does not trigger this recovery.

Concurrent HTTP requests in one browser tab share that recovery. The existing
page remains mounted while recovery runs; newly started business requests wait
behind the recovery until its principal is verified. Automatic AD login includes
an `expected_principal` constraint with the previous user and tenant. The server
compares it with the verified JWT subject before committing a session; the
constraint cannot supply identity or permissions. Explicit login retains its
token-only request. Logout, identity replacement and
unmount fence the operation; cancelling one request cannot cancel its peers.
Once the login mutation starts, the provider settles its identity independently
of request waiters. The server principal is read before the new local marker is
published. Old requests may replay once only when the verified user and tenant
match and the request is a read or carries proof of rejection before execution.
`require_principal` marks stale-context 409 responses with the same
`X-Auth-Rejected-Before-Execution` proof as its 401 responses. Ambiguous writes
and streaming request bodies are not replayed. Missing AD configuration or failed
reauthentication retains the login-page fallback and current-route redirect.

This recovery is owned by the shared HTTP request transport. SSE's existing
credential refresh/reconnect and initial login-page AD discovery keep their
own lifecycles; the recovery does not replay started stream or tool effects.

The amended decision 4 permits transparent recovery only within this bounded
grant. The absolute non-sliding day, expired-token
rejection, company-authority checks, and other decisions remain in force.
No company login-service or AD change is required. The platform backend is an
authorized issuer of company-compatible credentials; shared signing settings
remain server-only and must match the receiving services.

## Evidence boundary

Focused source tests prove exact clock alignment, expiry boundaries, and
non-sliding TTL behavior. They do not prove deployment restart, identity-provider
availability, browser cookie retention, or runtime revocation behavior.

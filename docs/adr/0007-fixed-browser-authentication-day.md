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

1. All three clocks are fixed at exactly 86,400 seconds in source and Compose.
   They are not operator-configurable deployment settings.
2. The lifetime is absolute and non-sliding. Authenticated reads, profile
   updates, and token rotation preserve the remaining server-side TTL rather
   than extending it.
3. A token whose expiration is equal to the current time is expired.
4. The browser does not replay failed writes or silently renew an expired
   company-authority snapshot. When the 24-hour authority expires, the user
   authenticates again.
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
- Transparent refresh and request replay: with equal clocks there is no valid
  refresh window, while replaying writes introduces duplicate-submission and
  concurrency risks.

## Evidence boundary

Focused source tests prove exact clock alignment, expiry boundaries, and
non-sliding TTL behavior. They do not prove deployment restart, identity-provider
availability, browser cookie retention, or runtime revocation behavior.

# Feedback and Announcement Availability

This is the active retirement contract for the unimplemented Workbench feedback
and announcement surfaces. It does not describe a deployed release or introduce
a feedback store, announcement delivery system, read-state store, or job queue.

## Browser contract

The Workbench does not offer feedback or announcement actions. Its menu no
longer loads an announcement count or opens an announcement dialog. The old
`/feedback` and `/notifications` URLs remain explicit unavailable pages rather
than projecting demo rows, zero business totals, or a retryable backend outage.

Existing route guards remain in force: both URLs require login; `/feedback`
requires an administrator. These pages make no feedback or announcement API
requests. Chat-message feedback capture was already retired; it is not restored
by this change. Browser notifications for real Run completion are separate and
remain supported.

## HTTP contract

`app/routes/workbench_projections.py` remains the sole owner of the old paths.
Authentication, deployment tenant scope, and the existing permissions are
checked before returning unavailable. Targeted mutations retain safe-ID
validation after authorization. Invalid IDs return `400`; unauthenticated
protected requests return `401`; missing permissions or invalid deployment
scope return `403`. Existing authentication failures are not converted into
successful empty responses.

Authorized requests to the following paths return `501` with exactly
`{"detail": "feedback_unavailable"}`:

- `GET /api/feedback/` (`feedback:read`)
- `GET /api/feedback/stats` (`feedback:read`)
- `PUT /api/feedback/{feedback_id}` (`feedback:admin`)
- `DELETE /api/feedback/{feedback_id}` (`feedback:admin`)

Authorized requests to the following paths return `501` with exactly
`{"detail": "notifications_unavailable"}`:

- `GET /api/notifications/active` (`notification:read`)
- `POST /api/notifications/{notification_id}/dismiss` (`notification:read`)
- `GET /api/notifications/admin` (`notification:admin`)
- `POST /api/notifications/` (`notification:admin`)
- `PUT /api/notifications/{notification_id}` (`notification:admin`)
- `DELETE /api/notifications/{notification_id}` (`notification:admin`)
- `POST /api/notifications/{notification_id}/replay` (`notification:admin`)

The existing admin-permission implication and platform-admin override are
unchanged. `501` means the capability is not implemented; it does not promise
temporary availability or enqueue work. Request bodies are not consumed as
commands. None of these endpoints opens a transaction, appends an audit row,
creates a job, returns an audit reference, or claims successful mutation. An
unsupported read does not claim that the tenant has zero feedback or no unread
announcements. No response echoes the request body, audience, user, or tenant.

### Identified anonymous compatibility consumer

Only anonymous `GET /api/notifications/active` continues to return `200` with
`{"notifications": []}`. This privacy-safe bootstrap shape is consumed by
`tools/verify_poc_gate.py::check_api_compat` and covered by
`tests/test_lambchat_frontend_compat.py`. It is not an authenticated domain list
and cannot expose audience or read state. Removal requires migrating that
identified bootstrap contract and its compatibility tests. The browser no
longer calls it. Invalid or expired authentication must still fail rather than
falling through to this anonymous response.

## Superseded surfaces and boundaries

Removed surfaces include the fixed feedback item/statistics and announcement
builders; their success DTOs and write DTOs; feedback/announcement service
methods and display panels; the announcement dialog, count, and dismiss UI;
and their obsolete success assertions, locale copy, and projection-audit route
allowlists. The former audit-only mutation success responses are replaced by
the explicit rejections above. Old URLs are retained only as unavailable
boundaries, not as hidden implementations or alternate clients.

The legacy `/api/users` projection and audit-only user management operations
are outside this retirement. In particular, the arbitrary administrator target
profile projection and its user-write queue claims are not evidence of a real
user directory or execution pipeline, and are not corrected here. The actual
administrator diagnostics reader remains intact. [Role Governance](role-governance-public-api.md)
has its own explicit audit-request contract and is unchanged. Shared
`WorkbenchGovernanceResponse` and `WorkbenchOperationResponse` remain for those
identified consumers. Historical release evidence is preserved as history.

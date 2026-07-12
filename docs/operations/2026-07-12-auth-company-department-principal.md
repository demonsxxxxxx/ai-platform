# Company Login Department Principal Phase Status

Status: `PR ready`

- [x] Phase 1: revision-35 freshness accepted on branch `codex/auth-department-principal` at base/head `6a30f23187609ed0c923d64c680994ce4d0df8b7`; scope fingerprint `sha256:9921f883469d6d31a24d3ba3877205a94f2b9f5c899cbaf427b498be4eb4a685` and clean worktree fingerprint `sha256:339be8db75120ba775d08e4855d8ab163cc643f5f27808b023aff5bc17208e25` matched.
- [x] Phase 2: narrow tracking issue [#403](https://github.com/demonsxxxxxx/ai-platform/issues/403) opened without an auto-closing relationship.
- [x] Phase 3 RED: the corrected 12-case matrix produced one expected failure because trusted top-level `department` was absent from the signed principal; the malformed, alias, ambiguous, request-field, and forged-header cases already failed safely.
- [x] Phase 4 GREEN: a strict extractor accepts only a top-level scalar `department`, applies `strip().casefold()` and the existing safe-ID validation, and passes the value into `AuthPrincipal` before session signing.
- [x] Phase 5 focused integration: auth and Capability Distribution/public Skill tests reported `45 passed, 78 deselected`; the trusted `QA` department sees only the `qa` Skill while an empty or nonmatching department sees none.
- [x] Phase 6: full auth routes reported `27 passed`; directly affected principal, resolver, repository authorization, and public Skill projection tests reported `30 passed`; compileall, diff, writable-scope, and added-line secret checks exited 0. Self-review found no Critical or Important issue.
- [x] Phase 7: implementation commit `6fe43017` pushed and draft PR [#404](https://github.com/demonsxxxxxx/ai-platform/pull/404) opened with non-closing issue reference `Refs #403`.
- [x] Phase 8 review RED: revision-38 case-preservation and alias-metadata tests reported `6 failed, 9 passed`; trusted `QA` was incorrectly folded to `qa`, and four valid top-level departments were erased when unsupported alias metadata coexisted.
- [x] Phase 9 review GREEN: the same focused matrix reported `15 passed`; exact-case `QA` authorization succeeds, case-mismatched `qa` authorization denies, valid top-level authority survives same/conflicting/blank/null aliases, and alias-only inputs remain denied.
- [x] Phase 10 follow-up verification: full auth routes reported `31 passed` with two existing Starlette cookie deprecation warnings; directly affected principal, distribution, repository authorization, and public Skill projection tests reported `30 passed`; compileall exited 0.
- [~] Browser runtime and real company-account acceptance deferred; Docker, deployment, 211, merge, review, and gate closure are outside this lane.

## Design Decision

The existing company user-info integration supports a flat top-level response and already recognizes a bounded set of role keys. Three department extraction options were considered:

1. Accept exactly top-level `department` as a scalar string.
2. Accept a bounded alias set such as `department_id` and `departmentName`.
3. Search nested objects recursively for department-like values.

Option 1 is implemented. It is the only option that adds the required authority without inventing precedence rules or broad recursive trust. Unsupported aliases are ignored metadata: they never become authority, and they do not override or invalidate a valid top-level `department`.

## Trust And Failure Boundaries

- Authority originates only from the server-side `call_existing_user_info(work_id)` response after successful company login.
- Login JSON continues to reject an extra `department` field, and an `X-AI-Department-ID` header on the login request is ignored.
- A valid top-level department is trimmed and otherwise preserved exactly, including case, so authorization remains an exact comparison rather than silently broadening identity equivalence.
- Missing, blank, numeric, list, object, unsafe multi-valued, and alias-only values produce an empty department. Unsupported alias metadata is ignored even when it is same-valued, conflicting, blank, or null.
- User-info failure remains an ordinary-user login with an empty department.
- Existing role and AI permission derivation is unchanged. Enterprise permissions are not copied into the signed session.
- The auth audit payload remains limited to source, work ID, roles, effective AI permissions, and admin status; raw user-info and client-forged values are not recorded.

## Evidence Boundary

Current evidence proves a local source slice only. It proves extraction, signed-session preservation, and direct use of the resulting principal by the existing public Skill projection. It does not prove a real company user-info response, browser login, deployed image, runtime source parity, 211 behavior, department rollout, independent review, merge readiness, or gate closure.

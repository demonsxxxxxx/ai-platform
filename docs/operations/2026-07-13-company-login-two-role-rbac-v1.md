# Company Login Two-Role RBAC V1 Phase Status

Current lane: `company-login-two-role-rbac-v1`, generation 1, phase 1.
Current branch: `codex/company-login-two-role-rbac-v1`.
Base and initial HEAD: `e66263c982bce4620d1f10849c2d016bee7b1b10`.
Issue: `https://github.com/demonsxxxxxx/ai-platform/issues/412`.

## Status

- [x] Phase 0 - revision-77 envelope echo and fingerprint gate. Independent
  results matched `scope sha256:0df2cf1f5f0c928a4dd3420bcae00e5a446c5f0cbded07cdb7a9a906a259e883`
  and `worktree sha256:4a4046804c6d3ce8e0bfafd1cbead71c0b24d608093cb9122ec065a35cb2cdc4`.
- [x] Phase 1 - root-cause trace and approved design/plan. Root cause spans raw
  upstream company roles, broad ordinary permissions, unversioned company
  cookies, permission-shaped admin inference, and independent navigation lists.
- [x] Phase 2 - isolated setup and baseline. Frozen install completed; package
  and lock hashes remained `54C07592...C09` and `A1CFBD3C...E5E6`.
  Backend auth baseline: `44 passed`. Frontend focused baseline: `6 passed,
  1 failed`; the pre-existing old AppRouteFallback test resolves a nonexistent
  `src/components/App.tsx` and is outside this lane's write set.
- [x] Phase 3 - backend RED/GREEN for canonical role, exact permissions, and
  company authz policy version. RED: `15 failed, 42 passed`; after one exact-set
  correction, GREEN: `57 passed` with two existing TestClient deprecation
  warnings.
- [x] Phase 4 - frontend RED/GREEN for pure access policy, navigation, route,
  role display, and Chinese defaults. RED: 7 contract failures with 4 existing
  `useAuth` probes passing. GREEN: 15/15 focused tests and `tsc -b` exit 0.
- [x] Phase 5 - compile, focused tests, lint, typecheck, build, projection audit,
  and mocked browser smoke. Evidence: compile exit 0; backend `57 passed`;
  frontend `15 passed`; lint exit 0 with 13 pre-existing warnings; `tsc -b`
  and production build exit 0; projection audit exit 0 with
  `pass_with_policy_gaps`; synthetic admin/user smoke passed at 1440x900 and
  390x844. All four screenshots were visually inspected with no blank frame,
  overlap, horizontal overflow, unauthorized management content, or real
  credential use.
- [x] Phase 6 - independent security and UX review of feature commit
  `90b5908f096d575f28a76c930574647fbaf4ff3b` found no Critical, Important,
  or Minor findings. The reviewer independently observed backend `57 passed`,
  frontend `15 passed`, clean diff checks, and all four nonblank screenshots.
  A final exact-head re-review follows this evidence-only commit.
- [x] Phase 7 - scoped feature commit pushed and ready PR
  `https://github.com/demonsxxxxxx/ai-platform/pull/413` opened. Initial CI
  readback passed backend required, projection audit/lint/build/trace, packaged
  image build, and frontend required. Final-head CI is re-read after pushing
  this evidence-only commit; no merge, deployment, or 211 action is included.

## Safety Boundary

No real credentials are used or retained. No Docker, deployment, merge, 211,
database, schema, dependency-manifest, CI-workflow, or MCP-lane source mutation
is authorized.

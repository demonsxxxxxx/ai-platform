# Release Authority Recovery Design

Status: `approved for implementation`

## Objective

Restore release authority from `origin/main` commit
`d189877fde72ccffef4db3d237dba402b6029a08` through stable required checks,
repository-local deployment, image provenance, and strict 211 parity readback.
This work does not close B1, B2, or B3 runtime acceptance. Until parity succeeds,
211 remains `blocked`.

## Current Baseline

| Subject | State | Evidence |
| --- | --- | --- |
| GitHub `main` | `d189877fde72ccffef4db3d237dba402b6029a08` | `current` source authority |
| Historical local lane | dirty `6a77c3795d5880a628fa11300a2e450e493023fb` | `historical`; forbidden deployment source |
| Recovery lane | `codex/release-authority-d189877-20260710` from `d189877` | `current` implementation source |
| 211 repository | dirty `12d626203ba37ce724d20e579bac2ac763e1341a` | `stale`; preserve first |
| 211 API/worker | historical ordinary-sandbox image | `stale` runtime subject |
| 211 frontend | main-derived and manually managed | `stale ownership` |
| GitHub protection | no protection and no Ruleset | `blocked` |

## Authority Invariants

1. Deployment source is a clean Git checkout at a full commit SHA.
2. Deployment rejects tracked, staged, or untracked source changes.
3. Backend and frontend images use the same commit and carry OCI/source labels.
4. API, worker, and frontend are owned by repo-local compose; manual frontend is invalid.
5. Source synchronization uses Git only; file copying and container patching are prohibited.
6. Unknown 211 dirty files are never deleted; patches, archive, metadata, and hashes are stored outside the checkout first.
7. Backend and frontend required checks appear on every PR targeting `main`.
8. `211 verified` requires source, images, containers, and served frontend provenance to equal one commit.

## Design

### Stable Required Checks

Use exact contexts `backend required` and `frontend required`. Both workflows
run on every PR targeting `main` and every push to `main`; required jobs never
disappear behind path filters. The Ruleset requires both contexts, pull requests,
up-to-date branches, and blocks force pushes and deletions.

### Clean Commit Deployment

Add one repository-owned command that resolves the requested commit, rejects
dirty or mismatched source, builds immutable backend/frontend tags with identical
labels, validates labels before compose recreation, rejects a legacy/manual
frontend container, invokes only repo-local compose, and emits redacted parity
JSON. It never copies source into 211 or patches containers.

### Repo-Local Compose Ownership

Move frontend into `deploy/ai-platform/docker-compose.yml`. Backend/frontend
image variables use full-commit tags. Compose labels record role, source commit,
repository, and cleanliness.

### 211 Dirty Preservation

Before transition, create an external directory containing machine-readable Git
status, tracked/staged patches, a non-secret untracked archive, inventory JSON,
and a hash manifest. Preservation never cleans, resets, checks out, deletes, or
overwrites the dirty repository. Deployment uses a separate clean checkout.

### Final Parity Proof

Record checkout HEAD, immutable image references/IDs, image labels, container
labels/IDs, API provenance, worker subject, served frontend provenance, and
compose working-directory/config labels. Missing fields, manual ownership,
dirty source, legacy compose paths, or mismatches return `verified: false`.

## Failure And Rollback

- Failed builds leave runtime unchanged; failed label checks block recreation.
- Failed readback remains `blocked` and creates no acceptance evidence.
- Rollback reruns the same command for a previously verified commit.
- Unknown dirty artifacts remain preserved and untouched.

## Verification

- Reproduce focused failures from clean `d189877` and use red-green TDD.
- Run workflow contracts, compile checks, frontend `ci:verify`, and Docker provenance checks.
- Read back the active GitHub Ruleset.
- Preserve and hash 211 dirty state before deployment.
- Require one strict same-commit parity report.

## Spec Self-Review

- No placeholders or unresolved alternatives remain.
- B1/B2/B3 acceptance is excluded.
- Current, stale, and blocked evidence are distinct.
- Destructive cleanup and manual source copying are prohibited.
- Completion has a concrete same-commit gate.

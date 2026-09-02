# Latest-main image quickstart change contract

## Goal

Provide one host command that waits for the current authoritative `main` commit
to finish the backend, frontend, and packaging GitHub Actions workflows, resolves
the two published GHCR image digests from the packaging evidence, materializes
the exact release checkout, and delegates deployment to the selected managed
environment controller.

The operator command is:

```bash
./scripts/deploy-latest.sh --profile internal-test --latest
```

The same profile command without `--latest` remains available for a
controller-prepared `incoming/latest-main.json` subject.

## Bounded change surface

- `scripts/deploy-latest.sh`
- `scripts/quickstart-s72.sh` (internal-test alias)
- `tools/latest_main_quickstart.py`
- `tests/test_deploy_latest_entry.py`
- `tests/test_latest_main_quickstart.py`
- `tests/test_sandbox_quickstart.py`
- `.github/workflows/ai-platform-packaging-publish.yml`
- `tests/test_packaging_publish_workflow.py`
- `.github/workflows/ai-platform-backend.yml`
- `tests/test_backend_ci_workflow.py`
- `README.md`
- `docs/operations/release-operations-runbook.md`
- this contract

The existing Compose convergence, health checking, runtime inspection, data
volume preservation, and image rollback implementation remains owned by
`tools/sandbox_quickstart.py`.

## Admission and authority contract

The latest deployment candidate is the exact 40-character SHA currently at the
fixed repository's `refs/heads/main`. For that same SHA, the controller must
require completed successful `push` runs and successful final jobs for all of:

| Workflow | Required final job |
| --- | --- |
| `.github/workflows/ai-platform-backend.yml` | `backend required` |
| `.github/workflows/ai-platform-frontend.yml` | `frontend required` |
| `.github/workflows/ai-platform-packaging-publish.yml` | `release image ready manifest` |

The successful packaging run publishes the verified ready bundle both as its
30-day Actions artifact and as the fixed `release-image-evidence.zip` asset on
the public `latest-main-evidence` prerelease. The public asset label is derived
only from the exact SHA, run ID, and run attempt. The rolling release tag is a
transport location, never deployment authority. The controller requires the
exact label, `github-actions[bot]` uploader, GitHub-provided `sha256` digest,
fixed public download URL, and the selected run's internal manifest to agree.
The downloaded archive is bounded and extracted without following archive paths
or links. The exact target checkout's `release_image_manifest.py verify`
command must validate all manifest, SBOM, provenance, signature, and scan
evidence before either image digest is admitted.

The controller never accepts mutable image tags. It atomically replaces
`incoming/latest-main.json` only after workflow, public asset, checkout,
manifest, and managed environment validation all succeed. A failure before that
replace leaves the previous subject byte-for-byte unchanged.

## Host and secret contract

The repository, Actions metadata, and rolling release evidence are public. The
controller uses anonymous HTTPS and deliberately removes inherited `GH_TOKEN`
and `GITHUB_TOKEN` values before GitHub access; it never invokes `gh auth` or
sends an Authorization header. Packaging uses only its job-scoped ephemeral
`${{ github.token }}` with protected `contents: write` permission to roll the
public asset after the complete bundle verifies. Packaging publication is
serialized. Each publisher first proves that its exact event SHA is still
current `main` and validates any existing rolling release as a public
prerelease before replacing its asset. If `main` advances in the remaining
publication race, the asset's stale label cannot match the controller's newly
selected run, so admission remains fail-closed. The complete bundle, including SBOM and
vulnerability scan evidence, is public by design. The host must already be
logged in to `ghcr.io` for private image pulls.

Anonymous Actions polling uses one repository-runs query per 90-second cycle to
stay within the public GitHub API budget. API rejection or rate limiting blocks
deployment before subject replacement or Compose mutation.

An existing valid quickstart subject supplies the stable managed `.env` path.
For the first deployment, the operator supplies the path with `--env-file`; the
controller records only that path and never reads or prints the file contents.

One owner-managed advisory lock covers candidate discovery, checkout
materialization, public evidence verification, subject replacement, pull,
Compose mutation, health checks, and rollback. Contention fails without mutating
the runtime.

## Failure and rollback contract

- Missing, pending beyond the bounded wait, failed, cancelled, or mismatched
  Actions evidence blocks deployment before Compose mutation.
- Download, archive, manifest, checkout, or environment validation failure
  blocks deployment before subject replacement and Compose mutation.
- Public evidence download and archive validation complete before target
  checkout materialization. The run-scoped Actions artifact remains for 30 days
  only as rollback-compatible evidence; the host does not consume it. A
  successfully materialized exact-commit checkout is retained as an
  owner-managed retry and rollback asset; this command does not delete release
  checkouts automatically.
- Image pull failure blocks target startup.
- Target startup or health failure delegates to the existing one-attempt image
  rollback. Persistent data volumes remain in place; database migrations are
  not reversed.
- The approved target subject remains available for an operator retry after a
  post-admission deployment failure.

## Falsifiable acceptance

Focused tests must prove exact-SHA three-workflow gating, final-job gating,
anonymous low-rate Actions discovery, public asset label/uploader/URL/digest
binding, bounded archive extraction, external run/attempt binding, semantic
manifest verification, inherited-token removal, atomic subject
preservation/replacement, checkout handoff, lock contention, zero-argument
compatibility, and CI ownership of both quickstart test files. Static
compilation and shell syntax checks must also pass. Docker runtime acceptance remains explicitly unavailable on hosts
without Docker and Compose.

## Production consumer

The exact-main resolver and public release-evidence admission in this contract
are also reused by
`sudo -n ./scripts/deploy-latest.sh --profile production --latest`. That profile
has a separate production change contract in
`docs/operations/production-bootstrap.md`. It uses `/data/ai-platform-prod`, the
direct governed OpenSandbox overlay, host-service convergence, production
quiescence, and production parity/rollback checks. The `internal-test` profile
selects only the internal-test overlay.

# Deployment Release quickstart contract

## Goal

Provide one host command that deploys the latest qualified, immutable Deployment
Release without reconstructing GitHub Actions on the target host:

```bash
./scripts/deploy-latest.sh --profile internal-test --latest
```

The same profile command without `--latest` retries the controller-approved
`incoming/latest-main.json` subject.

## Bounded change surface

- `.github/workflows/ai-platform-packaging-publish.yml`
- `tools/latest_main_quickstart.py`
- `tools/sandbox_quickstart.py`
- `tools/production_bootstrap.py`
- `tests/test_packaging_publish_workflow.py`
- `tests/test_latest_main_quickstart.py`
- `tests/test_sandbox_quickstart.py`
- `tests/test_production_bootstrap.py`
- `README.md`
- `docs/operations/release-operations-runbook.md`
- `docs/operations/production-bootstrap.md`
- this contract

Compose convergence, runtime health checks, data-volume preservation, and image
rollback remain owned by `tools/sandbox_quickstart.py` for internal test and
`tools/production_bootstrap.py` for production.

## Publication authority

Packaging remains the build and supply-chain authority. For one exact `main`
push, it builds Backend and Frontend images, generates and binds SBOMs, rejects
fixable HIGH or CRITICAL vulnerabilities, signs and attests the immutable image
subjects, reverifies downloaded provenance, and assembles the existing strict
`release-image-manifest.json`.

The complete ready evidence remains a run-bound, 30-day Actions artifact for
review and rollback investigation. The public deployment Release contains only
`release-image-manifest.json`.

The release-manifest job has serialized, environment-protected `contents: write`
authority. The repository's GitHub immutable-releases setting must be enabled as
a one-time administrator operation. The workflow does not receive repository
Administration permission. For each push, it proves its event SHA is still
`main`, then uses pinned GitHub CLI to create the unique
`deployment-<commit>-<run-id>-<run-attempt>` Release with the manifest asset and
`--latest=false`. GitHub CLI stages the asset on a draft before publishing when
immutable releases are enabled. The workflow then verifies through ordinary
Release metadata that `isImmutable=true`; otherwise the job fails.

A failed run cannot overwrite another run's tag or asset. A mutable Release
created while the repository setting is misconfigured is never qualified and is
ignored by host admission; rerunning the workflow uses a new run-attempt tag.
Every successfully qualified Release and tag is intentionally permanent and
serves as a rollback/audit identity.

## Host admission

`--latest` means the newest qualified Deployment Release, not the current tip of
`main` or GitHub's mutable "Latest" marker. The host makes one anonymous
`/releases?per_page=100` API request, skips non-deployment and mutable Releases,
and validates the first published immutable deployment candidate. Admission
requires:

- the exact versioned tag and target commit;
- a published, non-prerelease, immutable Release authored by
  `github-actions[bot]`;
- exactly one `release-image-manifest.json` asset with the exact
  commit/run/attempt label, uploader, public URL, bounded size, and GitHub
  `sha256` digest;
- downloaded bytes matching that digest; and
- a strict manifest bound to the same repository, workflow, commit, run,
  attempt, `linux/amd64` platform, and exact Backend and Frontend GHCR digest
  references.

The host does not query Actions runs or jobs, wait for `main`, extract a public
evidence archive, or rerun CI's SBOM, Trivy, signature, provenance, or attestation
verification.

The existing release authority materializes or reuses
`<managed-root>/releases/<commit>`, proving the commit belongs to protected
`main`, the origin is canonical, and the checkout is exact and clean. Runtime
preflight rechecks the local commit, origin, checkout cleanliness, Compose files,
and immutable images. It deliberately does not require the qualified release to
remain the current `main` tip.

Only after Release, asset, manifest, checkout, and managed-environment admission
succeed does the controller atomically replace `incoming/latest-main.json`. A
failure before replacement preserves the previous subject byte-for-byte.

## Host and secret contract

Repository source, Release metadata, and the manifest asset are public. The
controller removes inherited `GH_TOKEN` and `GITHUB_TOKEN`, invokes no GitHub CLI,
and sends no Authorization header. Packaging uses only its protected,
job-scoped `${{ github.token }}`. The Docker host must already be logged in to
GHCR for private immutable-image pulls.

An existing subject supplies the stable managed `.env` path. On first deployment,
the operator supplies `--env-file`; the controller records only its path and
validates owner/mode metadata without reading or printing its contents.

One owner-managed lock covers Release resolution, checkout materialization,
subject replacement, image pull, Compose mutation, health checks, and rollback.
Contention fails without runtime mutation.

## Failure and rollback contract

- Missing, malformed, or mismatched Release metadata, or the absence of a
  qualified immutable Deployment Release, blocks before subject replacement or
  Compose mutation.
- Manifest download, digest, semantic binding, checkout, or environment failure
  blocks before subject replacement or Compose mutation.
- The Actions evidence artifact remains audit evidence; the host does not consume
  it.
- Successfully materialized checkouts are retained for approved retry and
  rollback; this command does not delete them.
- Image pull failure blocks target startup.
- Startup or health failure delegates to the existing one-attempt runtime
  rollback. Persistent data volumes remain in place and database migrations are
  not reversed.
- A post-admission deployment failure retains the approved target subject for an
  operator retry.

## Falsifiable acceptance

Focused tests must prove immutable Release publication, fresh-main checks before
publication, single-use versioned tags, minimal public manifest publication,
Release/asset/digest binding, strict manifest-to-image binding, inherited-token
removal, atomic subject preservation/replacement, checkout handoff, lock
contention, source validation without current-tip polling, production caller
compatibility, and retention of complete run-bound Actions evidence. Static
compilation, Ruff, YAML, shell syntax, and required Ubuntu checks must pass.
Docker runtime acceptance remains separate and requires an authorized
Docker-capable host.

## Production consumer

`sudo -n ./scripts/deploy-latest.sh --profile production --latest` reuses this
Release admission and then applies the separate production host, quiescence,
OpenSandbox, parity, and rollback contract in
`docs/operations/production-bootstrap.md`. The `internal-test` profile continues
to select only the internal-test Compose overlay.

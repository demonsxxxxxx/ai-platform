# Docker Packaging Authority

## Phase 1 authority

`pyproject.toml` declares direct Python dependencies and pins the supported lock
tool. `uv.lock` is the reviewed direct and transitive dependency resolution.
Generate and consume it only with `uv 0.12.1`:

```powershell
python -m pip install "uv==0.12.1"
uv lock
uv lock --check
uv sync --locked --extra test --no-install-project
```

The backend Dockerfile uses the same lock with `uv sync --locked`; it must not
derive an unlocked requirements file from `pyproject.toml`. Keep index, proxy,
and credential values out of Git, issue/PR text, and command output. The default
CI and image build use only public package sources.

The backend build and CI run Python 3.13.14. The frontend build and CI run Node
22.23.2 with the `package.json` `pnpm@10.32.1` package-manager contract and the
reviewed `pnpm-lock.yaml`. Focused tests reject version drift among declarations,
Dockerfiles, and workflows.

All external Dockerfile bases use a readable patch tag plus an immutable OCI
index digest:

- `python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8`
- `ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded`
- `node:22.23.2-bookworm@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a`
- `nginx:1.27.5-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`

These are multi-platform index subjects verified from the official registries;
Docker resolves a platform manifest at build time. The Phase 1 GitHub jobs
observe Linux amd64 builds only and do not prove every indexed platform. Update a
base by verifying the official tag, index media type, digest, and required
platforms, then update the Dockerfile, version declaration, lock when applicable,
and focused tests in one review.

Pull requests and `main` run the stable `backend required` and `frontend required`
checks. Those checks include real Docker builds. Backend acceptance verifies
imports, HTTP startup, non-root identity, executable entrypoint, source markers,
and existing OCI/release labels. Frontend acceptance verifies the built artifact,
the same commit/label contract, and an HTTP health response from the temporary CI
container. That standalone frontend container overrides its upstream with the
numeric loopback address only for the health probe: the production image keeps
its Compose-network `api:8020` default, while an isolated CI runner has no `api`
DNS subject. On failure, both image jobs report only a bounded log-tail line count,
fixed non-secret startup signals, and container status/exit code; they never dump
container environments or raw log content. The workflows have `contents: read`
only and do not publish, deploy, read deployment configuration, or claim a registry
or 211 runtime subject.

Rollback is an ordinary revert of the reviewed lock, Dockerfile, workflow, and
version declaration changes. It does not retag, publish, deploy, or mutate the
production Compose authority.

## Phase 2 publication authority

`.github/workflows/ai-platform-packaging-publish.yml` is the only registry
publisher. It accepts a trusted `push` to `main`, or a `workflow_dispatch` whose
selected ref is `main`, whose confirmation is the fixed `PUBLISH_MAIN` value,
and whose `packaging-publish` GitHub Environment protection permits the job.
Repository and checkout identity are rechecked before the build. Pull request,
`pull_request_target`, fork, tag, branch, and user-supplied ref inputs have no
publish path. Ordinary verification jobs retain only `contents: read`; the
publish job alone receives `packages: write`, `id-token: write`, and the
attestation permission. Action dependencies are fixed to reviewed 40-hex
commits, and all jobs use unprivileged GitHub-hosted runners.

The publisher emits exactly two repository-owned subjects:

- `ghcr.io/demonsxxxxxx/ai-platform-backend`
- `ghcr.io/demonsxxxxxx/ai-platform-frontend`

The executor is an alternate runtime entrypoint in the backend image, not an
independent Docker build context or publish subject. Executor consumers must
therefore reuse the exact immutable backend subject. Publication is explicitly
`linux/amd64`; base-image OCI index digests, BuildKit cache identities, and
runner-local Docker image IDs are not published image digests and cannot enter
release-image evidence.

Each subject is pushed under only its full 40-hex source commit tag. Downstream
steps immediately switch to `subject@sha256:<registry-manifest-digest>` and
generate SPDX JSON, an OCI SBOM attestation, SLSA provenance, a keyless Sigstore
signature, and a blocking Trivy scan for `HIGH,CRITICAL`. Missing or mismatched
digests, attestations, signatures, SBOMs, or scan results fail before the ready
manifest job. The strict machine-readable contract is
`schemas/release-image-manifest.v1.schema.json`; the parser and assembler are
`tools/release_image_manifest.py`. The ready manifest binds source commit,
repository and workflow run identity, platform, Dockerfile hash and context,
registry manifest digest, and all evidence references for both subjects.

The scan runs against the published digest before any SBOM or provenance
attestation and before keyless signing. Automatic BuildKit provenance is
disabled so it cannot bypass that ordering. A scan failure can therefore leave
an unattested, unsigned digest in GHCR, but it cannot create subject evidence or
a ready manifest. Operators must treat every registry object without a verified
ready manifest as quarantined; the workflow does not delete it or hide the
failed run.

Provenance verification uses GitHub CLI `2.97.0` downloaded from its fixed
release URL. The workflow verifies both the checksum-list digest and the Linux
amd64 archive digest before executing that binary, and exposes `github.token`
as `GH_TOKEN` only to the exact verification step. The token is never a Docker
build argument, build secret, or logged value. The action-produced provenance
bundle is copied into run evidence and verified locally by that pinned CLI. The
manifest records the action attestation ID and URL, bundle hash, and verified
JSON hash. Artifact names and references include source commit, run ID, and run
attempt so reruns cannot silently mix evidence.

The assembly job installs the same checksum- and version-verified GitHub CLI and
cryptographically verifies each exact downloaded provenance bundle again. It
supplies `github.token` only to that verification step. The new verification
JSON must be canonically equal to the publish-job verification JSON, and its
embedded verified bundle must equal the downloaded bundle. The final manifest
records the run-bound assembly verification reference and hash. Coordinated
replacement of a bundle, its signature or verification material, and the
caller-provided JSON/hashes therefore cannot produce a ready manifest without a
successful fresh verification by the pinned CLI.

The JSON Schema rejects all expressible cross-role combinations, including
Dockerfile, source tag, immutable image reference, SBOM/signature reference,
provenance artifact reference, and scan artifact reference. JSON Schema cannot
express equality among arbitrary source, digest, and run fields. The Python
semantic verifier is therefore the readiness authority: with the downloaded
evidence root it rehashes and parses the SPDX document and Trivy report. Syft's
native document name, package references, and generated namespace are not image
identity authority. Immediately after pinned Syft generation, the trusted
workflow replaces the standard SPDX `documentNamespace` with a deterministic
absolute URI derived from role, exact source commit, and registry manifest
digest; hashing and OCI attestation use those final bytes. The verifier requires
that exact namespace. It also requires every reported Trivy vulnerability to be
an object with one of `UNKNOWN`, `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL` and then
rejects the configured blocking severities. It rehashes and parses the
provenance bundle, publish verification JSON, and assembly verification JSON;
the two verification results and their embedded bundle must match exactly. It
then checks the verified statement subject/digest and certificate-backed
repository, workflow/ref, source commit, GitHub-hosted runner, run ID, run
attempt, and trusted timestamp result. The v1 subject cardinality is invariant:
exactly one backend and one frontend are required even when compatibility
`--expected-role` flags are supplied.

Repository visibility does not establish GHCR package visibility. Before first
publication, an operator must configure and review the `packaging-publish`
Environment protection, package visibility/access, retention, tag mutation or
deletion policy, and artifact retention. Those are GitHub/GHCR operator inputs;
this repository workflow does not guess or mutate them. The full-commit tag is
a lookup aid, never authority: consumers use the captured digest.

Evidence states remain separate: source tests prove source contracts; CI image
builds prove runner-local packaging; the publish workflow and ready manifest
prove registry subjects and supply-chain evidence for one source commit; only a
fresh 72 release procedure and runtime acceptance can prove deployed behavior.
No Phase 2 artifact is 72 runtime evidence.

Rollback means stop consumers from selecting the affected ready manifest and
publish a reviewed later source commit through the same workflow. Do not retag
or overwrite an existing source tag, delete evidence to conceal a failure, or
fall back to a runner-local image ID.

## Later phases

Phase 3 may add a root local Compose facade and split the existing production
Compose consumers to reviewed `image@sha256` inputs. Only after real layer and
runtime measurements may a later decision split API, worker, or executor images.
Neither phase may rename or duplicate `deploy/ai-platform/docker-compose.yml`, and
both remain separate from 211 release authority and runtime acceptance.

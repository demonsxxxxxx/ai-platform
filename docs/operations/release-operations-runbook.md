# Release Operations

This is the executable application release procedure. An explicitly requested
version publishes an immutable Deployment Release with one operator asset,
`ai-platform-internal-test.tar.gz`,
containing the runtime-only package and matching `release-image-manifest.json`. A host consumes that package directly. Git,
GitHub Actions, source checkouts, and host image builds are not part of a normal
application install or upgrade.

## What CI publishes

The protected Packaging workflow runs on main pushes and confirmed manual main
runs, independently of the Backend and Frontend checks. Before requesting a
formal version, verify those required checks passed for the selected main commit.
Packaging verifies the two application image subjects, binds their complete
`linux/amd64` registry digests, and creates one package from the manifest.
Only an explicit version attaches `ai-platform-internal-test.tar.gz` to an immutable
Deployment Release. GitHub's automatically generated source archives may still
appear; they are not operator packages.

The package contains the exact Compose file, internal-test OpenSandbox overlay,
`.env.example`, `deploy.py`, the release manifest, package guide,
`BACKUP-RESTORE.md`, and verified image qualification evidence in
`release-evidence/`.
The package pins Backend, Frontend, PostgreSQL, Redis, and MinIO by
`repository@sha256:...`. The public Release is immutable and its package files
must be downloaded from that one Release; do not mix package files between
versions. Additional CI audit artifacts remain in Actions. Vulnerability
evidence separates the complete HIGH/CRITICAL inventory (including findings
without fixes) from the fixable HIGH/CRITICAL blocking gate; a passing gate does
not mean the complete inventory is empty.

Publication is protected by the `packaging-publish` environment. The workflow
creates a unique versioned Release only after all qualification steps pass and
then verifies that GitHub reports it immutable. A mutable or incomplete Release
is not a deployment input.

For a new named version such as `v0.1.1`, run the Packaging workflow on `main`
with `confirm_release=PUBLISH_MAIN` and `release_version=v0.1.1`. The optional
version accepts only `vMAJOR.MINOR.PATCH` with no leading zeroes. Choose a version
that does not already exist; publishing never replaces an existing version.
A blank version keeps manual runs audit-only, with no Git tag or Release.
Automatic main pushes also create no Git tag or Release. Both image builds
still refresh their runtime stages on every attempt so cached APT/APK layers
cannot retain fixed vulnerabilities. The normal main-source, environment,
scan, signature, and attestation gates still apply.

For a daily test package, open the successful main Packaging run's Actions
artifacts and download `ai-platform-internal-test-<commit>-<run>-<attempt>`.
That artifact contains only `ai-platform-internal-test.tar.gz` and is retained for
seven days. It is produced on main pushes and blank-version manual runs after
package qualification; it is a temporary test package, not a formal Release or
production deployment approval. Separate audit evidence remains for 30 days.
Formal version runs publish the single package in their immutable Release
instead. Existing Releases and tags are not cleaned up by this workflow, and
publication retains `--latest=false`; it does not select a new GitHub Latest.

A named version is reserved only after qualification and a fresh main-commit
check. An existing tag is a hard failure, never moved or reused. If publication
fails after reserving a tag, stop and inspect that tag and any draft Release;
do not automatically delete, reuse, or overwrite it. A completed Release still
requires immutable status and the single matching internal-test package asset. Publishing a package
does not install or upgrade any host.

## One-time host preparation

Use a Linux host with Python 3, Docker, Compose v2 with `--wait` and `!reset`,
and an active `opensandbox.service`. The internal-test host must retain native
Docker `bridge` networking and `runsc` gVisor runtime, with the OpenSandbox
NAT-redirect egress sidecar disabled. The test bridge has no production host
network guard: a task can reach routable host, private and public destinations.
Limit this package to a trusted isolated test host. The existing production
host contract remains documented in [production host preparation](production-bootstrap.md),
but this workflow does not publish a new production package for s75.

Create or retain one owner-held mode `0600` environment file. For a new host:

```sh
umask 077
cp .env.example .env
# Set the public origin, database, object storage, auth, model, and
# OpenSandbox values for this host.
chmod 600 .env
```

Never copy the example over an existing installation. Never print the file,
copy its contents into a package, or put secrets in shell history, issue text,
or release evidence. The invoking user must own the file. When Docker requires
sudo, pass `--docker-cmd 'sudo -n docker'` to the package entry instead of
running Compose as a different user.

The package controller still requires non-placeholder `TRUSTED_PRINCIPAL_SECRET`
and `AI_SESSION_SECRET` values of at least 32 characters. Preserve the existing
values across upgrades. An intentionally HTTP-only trusted
isolated intranet requires its actual HTTP origin, both secure-cookie flags
false and explicit `--allow-insecure-http`; restrict access and firewall the
direct API. This flag acknowledges risk, not network hardening.

The OpenSandbox test bridge, workspace Host bind, lifecycle service and proxy
binding are documented in the packaged `README.md`; verify actual `runsc`
execution and egress separately from controller readiness.

## Install or upgrade

Download the internal-test package from one immutable Deployment Release and use
its embedded manifest; extract the archive into a new directory, and keep that directory
unchanged. Take a coordinated database, MinIO, Redis and workspace backup and choose a
maintenance window with no active Runs, Attempts, leases, or sandbox containers.
Follow the [backup and recovery procedure](../../deploy/ai-platform/BACKUP-RESTORE.md), including
a restore rehearsal, before changing an existing installation.

An upgrade from the retired SSE transport additionally requires the explicit
[legacy-state retirement](redis-streams-sse-cutover-acceptance.md#explicit-legacy-state-retirement)
step before migration. Use the new immutable backend image, the same Compose
project and verified package files, and keep old API/Worker producers stopped
until the package migration completes. This is a one-time data transition, not
an API/Worker startup action. After schema `2026.09.12.1`, an older backend image
is incompatible; the failure/recovery rules below still apply.

From the extracted directory, run:

```sh
python3 deploy.py --env-file /absolute/path/to/.env
```

Add `--docker-cmd 'sudo -n docker'` when required. The entry performs one
project-wide deployment lock and the following gates:

1. Validate the owner-held environment file and Compose semantics without
   exposing values.
2. Confirm every packaged image is digest-qualified and locally resolves to
   the exact repository digest. The normal path pulls once; it does not build.
3. Check database Runs, Attempts, leases, and sandbox inventory.
4. Stop only application admission and check activity again before migration.
5. Keep PostgreSQL, Redis, and MinIO running with their existing containers,
   mounts, and volumes.
6. Run the versioned migration and workspace initialization as one-shot
   services. Fresh/current-layout installations skip legacy workspace migration;
   any existing supported legacy source directory, even empty, requires
   `--migrate-legacy-workspaces`
   and a verified backup. The read-only legacy source is retained.
7. Recreate and wait for the application services, then verify API health and
   readiness, OpenSandbox reachability, application commit/image identity, and
   an advancing Worker heartbeat.

The command never runs `down`, `down -v`, volume deletion, a second Compose
project, a source checkout, or an Actions query. A healthy old runtime is not
acceptance for a target package; report the target commit, image identities,
health, heartbeat, persistent-service identity, and quiescence after completion.
The package entry ends with `deployment: healthy (<commit>)` only after these
checks pass.

For a no-change preflight against already loaded images:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --check
```

`--check` does not pull, stop, recreate, migrate, or initialize application/data
services. After image digest verification, it may run a temporary network-disabled,
read-only backend container to check workspace migration markers. It is not
deployment acceptance. With `--resume-install --check`, PostgreSQL must
already be running; the preflight never starts it.

## Already downloaded images

If image archives were downloaded separately, load them into the same Docker
daemon while preserving the package's complete `repository@sha256:...` identity.
For example, an OCI archive may be imported with `ctr` using `--digests`, and a
Docker archive may be loaded with `docker load`. Then run:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --offline
```

Offline mode skips the network pull only. It still requires every image in the
package and verifies every local `RepoDigest` before stopping application
admission. A missing digest, manually retagged image, floating tag, or mixed
package is a hard failure.

## Failure and recovery

Configuration, image download, and digest failures occur before admission
changes. If activity appears during the second check, migration does not start
and the stopped application containers are restarted. After migration begins,
any startup or health failure stops application admission and retains data.
The entry does not guess at binary rollback and does not reverse a database
migration. Restore a compatible package or use the authorized database backup
procedure after classifying the failure. Do not edit migration checksums.

The only automated first-install recovery is `--resume-install` with the intact
owner-held mode `0600` `.ai-platform-install-state.json` beside the env file, the
same package, complete rendered configuration and migration mode, and no
application containers (even stopped ones). Existing data images and volume
identities must match. Changed inputs, missing journals, orphaned volumes,
partial activity schemas and ambiguous/late startup failures require operator
classification using the [recovery procedure](../../deploy/ai-platform/BACKUP-RESTORE.md).
Never delete data, journals or migration markers to bypass these gates.

A quarantined sandbox record is not silently deleted. Only a terminal,
unclaimed failed-reconciliation record with a terminal Run and a verifiable
runtime identity can be excluded; a surviving recorded container or missing
identity blocks deployment. Keep historical records intact.

## Build-only network recovery

The host package path does not build images. If a maintainer must rebuild an
image in CI or a controlled build environment, use a bounded HTTPS GET probe for
both the normal and security Debian endpoints before `sudo -n docker build`.
Keep the pair separate; do not disable APT security or leave both mirrors
implicit when an override is supplied. For example:

```sh
APT_MIRROR="https://mirrors.ustc.edu.cn/debian"
APT_SECURITY_MIRROR="https://mirrors.ustc.edu.cn/debian-security"
MIRROR_ARGS=()
if test -n "${APT_MIRROR:-}"; then
  MIRROR_ARGS+=(--apt-mirror "$APT_MIRROR")
fi
if test -n "${APT_SECURITY_MIRROR:-}"; then
  MIRROR_ARGS+=(--apt-security-mirror "$APT_SECURITY_MIRROR")
fi
python3 -B "$SOURCE/tools/release_authority.py" probe-apt-mirrors \
  --apt-mirror "$APT_MIRROR" \
  --apt-security-mirror "$APT_SECURITY_MIRROR"
```

The probe performs HTTPS GET checks and does not invoke Compose. If either
endpoint fails, use the upstream Debian endpoints rather than weakening APT
verification. This build-only recovery is not an alternate deployment path.

## Evidence and ownership

The package is the deployment authority for the application release. The
active task or pull request owns current release status, operator, blockers, and
runtime evidence. The package README owns its command details; this document
owns the release boundary and the one-command install/upgrade procedure.

Independent final runtime acceptance is required after the command exits; the
operator must verify the target package independently.

For host acceptance, retain redacted evidence for the exact package commit:
controller termination, target API/Frontend/Worker image and commit identity,
API readiness, OpenSandbox checks, two advancing Worker heartbeat samples,
`migrate` and `workspace-init` exit 0, unchanged persistent-service identity and
restart counts, no active work, and no volume deletion. A configured browser
origin must be the browser-visible frontend origin. The local lifecycle example
defaults to `18001`.

The old repository deployment wrappers and implicit "latest" selection are
retired. Use the package command above; do not reconstruct release metadata or
copy source files on the target host.

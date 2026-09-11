# Release Operations

This is the executable application release procedure. CI publishes an immutable Deployment Release containing the runtime-only package and the matching
`release-image-manifest.json`. A host consumes that package directly. Git,
GitHub Actions, source checkouts, and host image builds are not part of a normal
application install or upgrade.

## What CI publishes

The protected Packaging workflow runs after the required Backend and Frontend
checks. It verifies the two application image subjects, binds their complete
`linux/amd64` registry digests, and creates two packages from the same manifest:

- `ai-platform-internal-test.tar.gz`
- `ai-platform-production.tar.gz`

Each package contains the exact Compose file, the selected OpenSandbox overlay,
`.env.example`, `deploy.py`, the release manifest, and a short package guide.
The package pins Backend, Frontend, PostgreSQL, Redis, and MinIO by
`repository@sha256:...`. The public Release is immutable and its package files
must be downloaded from that one Release; do not mix package files between
versions. The complete CI evidence remains an Actions audit artifact and is not
needed by the host.

Publication is protected by the `packaging-publish` environment. The workflow
creates a unique versioned Release only after all qualification steps pass and
then verifies that GitHub reports it immutable. A mutable or incomplete Release
is not a deployment input.

## One-time host preparation

Use a Linux host with Python 3, Docker, Compose v2 with `--wait` and `!reset`,
and an active `opensandbox.service`. Prepare the OpenSandbox host policy,
`runsc` runtime, lifecycle address, credentials, network guard, and workspace
permissions through the production host procedure before using the production
package. This host setup is not repeated for every application upgrade.

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

The production host contract is documented in
[production host preparation](production-bootstrap.md). Its host files and
systemd units are prerequisites, not application-release inputs.

## Install or upgrade

Download the desired package and matching manifest from one immutable Deployment
Release, extract the archive into a new directory, and keep that directory
unchanged. Back up the database and choose a maintenance window with no active
Runs, Attempts, leases, or sandbox containers.

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
   services.
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

`--check` does not pull, stop, recreate, migrate, or initialize services. It is
not deployment acceptance.

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

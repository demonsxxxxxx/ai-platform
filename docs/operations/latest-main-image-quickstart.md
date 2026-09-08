# Deployment package quickstart

The public immutable Deployment Release contains a runtime-only package. Choose
one package and its matching manifest from the same Release:

- `ai-platform-internal-test.tar.gz` for the internal-test OpenSandbox overlay.
- `ai-platform-production.tar.gz` for the production OpenSandbox overlay.

Extract the archive into a new directory and do not mix files with another
version. The package contains the exact Compose files, immutable application and
data image references, `deploy.py`, `.env.example`, and
`release-image-manifest.json`. It does not need a Git checkout, GitHub Actions
access, source files, or a host build.

## First install

The host needs Linux, Python 3, Docker, Compose v2 with `--wait` and `!reset`,
and the already-provisioned `opensandbox.service`. Production OpenSandbox
credentials, network policy, `runsc`, the lifecycle address, and workspace
permissions are one-time host preparation; see [production host preparation](production-bootstrap.md).

For a new installation, create the environment file in the extracted directory:

```sh
umask 077
cp .env.example .env
# Set the host-specific public origin, credentials, storage, model, and
# OpenSandbox settings.
chmod 600 .env
```

For an upgrade, reuse the existing environment file. Never overwrite it with
the example or print its values. The invoking user must own it and its mode must
be exactly `0600`.

## Deploy

Back up the database and make sure there are no active Runs, Attempts, leases,
or sandbox containers. From the extracted package directory:

```sh
python3 deploy.py --env-file /absolute/path/to/.env
```

If Docker requires sudo:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --docker-cmd 'sudo -n docker'
```

The entry validates the package and all digest identities, checks activity,
stops application admission, checks activity again, preserves PostgreSQL/Redis/
MinIO containers and volumes, runs migration and workspace initialization,
starts the application, and verifies API readiness, OpenSandbox reachability,
application identity, and an advancing Worker heartbeat. It never removes data
volumes and does not automatically reverse a migration.

Use `--check` to validate configuration, activity, and already cached image
digests without pulling or changing services:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --check
```

This is a preflight result, not deployment acceptance.

## Offline images

Load downloaded OCI or Docker image archives into the same Docker daemon while
retaining their complete `repository@sha256:...` identities. Then use:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --offline
```

Offline mode skips network pulls but still verifies each expected `RepoDigest`.
Missing images, floating tags, manually retagged images, or package mixing fail
before application admission changes.

## Failure behavior

Failures before admission leave the current runtime untouched. If new activity
appears after admission is stopped, the old application containers are restored
and migration is not started. Once migration begins, a failure stops application
admission and retains data. The package does not guess whether an old binary is
compatible with an advanced schema, so use a compatible package or the approved
database restore procedure. Do not edit migration checksums or run a second
package process while one is active.

The normal package path never fetches `main`, queries Actions, materializes a
source checkout, changes the Docker daemon proxy, provisions the OpenSandbox
host, or runs a second Compose project.

# Deploy a released version

Download `ai-platform-internal-test.tar.gz` or `ai-platform-production.tar.gz`
from the **chosen immutable Deployment Release** on the official repository.
Do not mix files from different versions or use an untrusted archive: the package
contains executable deployment code. Image digests and the application commit
are already fixed in the package. Git, Actions access, a source checkout and
host-side image builds are not required.

The packaged environment example contains operator configuration. Application
image references, source commit and executor image digest are bound by the
Release and omitted from that example.

The package also fixes the OpenSandbox provider, security profile, server proxy
and network mode. Production selects governed egress; internal-test selects the
bridge test profile. Choose the matching package rather than editing these values.

OpenSandbox configuration has two owners:

| Configuration | Owner |
| --- | --- |
| Lifecycle connection | Application env: production uses `OPENSANDBOX_BASE_URL`; internal-test requires `OPENSANDBOX_DOMAIN` and `OPENSANDBOX_PROTOCOL`. Both require `OPENSANDBOX_API_KEY`. |
| Workspace and capabilities | Application env: `SANDBOX_WORKSPACE_ROOT`, `SANDBOX_CALLBACK_TOKEN`; production also requires `SANDBOX_EGRESS_PROOF_SIGNING_KEY` and `MODEL_PROXY_INTERNAL_TOKEN`. |
| Timeouts | Optional application tuning: `OPENSANDBOX_REQUEST_TIMEOUT_SECONDS`, `OPENSANDBOX_TIMEOUT_SECONDS`. |
| Kernel isolation and host firewall | Host OpenSandbox TOML, Docker `runsc` runtime and network-guard service, prepared once by the host administrator. |

Workspace files are staged and collected through the OpenSandbox file API after
lease acquisition. Network egress follows the selected profile and host network
policy. The deployment environment, security profile and expected network mode
are fixed in Compose; build commit and dirty markers are supplied during image
construction. These are not operator settings in the deployment environment file.

`AI_PLATFORM_API_UPSTREAM` selects the platform API reached by the frontend proxy.
Model connection ownership depends on the selected package:

| Package | Model request configuration |
| --- | --- |
| Production | Configure the upstream URL, key and enabled models in the administrator's Models page before running a model. The executor uses the platform proxy, which requires the Run's pinned database connection revision. Environment model URLs and credentials cannot substitute for that binding. |
| Internal-test | The package enables direct model credential forwarding. `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` supply the executor's direct connection, including when a database model catalog is active. |

The browser's retained `/settings` address redirects to `/models`. The removed
generic settings API did not persist or apply submitted values; model changes
now have one browser entrance and one database-backed control plane.

`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `CLAUDE_AGENT_MODEL`, `DEFAULT_MODEL_ID`
and `MODEL_CATALOG_JSON` supply legacy model selection before the database
catalog is active. That selection can create a Run without a connection revision;
it does not provide a working production proxy fallback. Model gateway request
concurrency is currently unbounded by the platform; the capacity report records
enforcement as unimplemented.

## Prepare the host once

Use a Linux host with Python 3, Docker, Docker Compose supporting `--wait` and
`!reset`, and a configured, active `opensandbox.service`. OpenSandbox host
provisioning (credentials, network policy and workspace permissions) is a
separate first-install prerequisite, not repeated during application upgrades.
The production package requires the production OpenSandbox security profile;
the internal-test package must not be used to relax a production installation.

Extract the chosen archive into a new directory. Keep this directory unchanged.
For an existing installation, use its existing environment file and the same
Docker host. **Do not copy the example over an existing environment file.**
For a new installation:

```sh
umask 077
cp .env.example .env
# Edit .env: replace example secrets, set the public origin, authentication,
# model encryption and OpenSandbox connection settings for this host.
chmod 600 .env
```

The configuration must be owned by the invoking user. If that user requires
sudo for Docker, use `--docker-cmd 'sudo -n docker'`; do not run the entire
entry as another user against a differently owned configuration.

## Install or upgrade

Back up the database before upgrading. Choose a maintenance window with no
active tasks or sandbox leases. From the extracted directory:

```sh
python3 deploy.py --env-file /absolute/path/to/.env
```

This checks configuration, downloads images, verifies locally available digest
identities, checks activity, stops application admission, checks activity again,
runs migration and workspace initialization, starts the selected application,
and verifies API readiness, container identity, OpenSandbox reachability and an
advancing Worker heartbeat. The existing PostgreSQL, Redis and MinIO containers
are not recreated. No data volume is deleted.

To check configuration, activity and already-cached images without downloading
or changing services, add `--check`. This is preflight only, not deployment
acceptance. Expired quarantined records are not automatically deleted: only
terminal, unclaimed failed-reconciliation records tied to a terminal Run may
be excluded, and any surviving recorded sandbox container still blocks.

Success ends with `deployment: healthy (<commit>)`. A returned nonzero status is
not a successful deployment. Do not start another invocation while one is still
running; the project-wide lock prevents concurrent package deployments.

## Already downloaded or offline images

Load images into the same Docker daemon, retaining the package's complete
`repository@sha256:...` identities, then run:

```sh
python3 deploy.py --env-file /absolute/path/to/.env --offline
```

This skips downloads, not local image verification. Missing images or RepoDigests
stop before admission changes. Never manually tag an image to pretend that it
has the required digest. No temporary script edits or Git bundles are needed.

## Failure and recovery

- Configuration, download or image-verification failure: existing services stay
  untouched. Fix the reported prerequisite and retry.
- Activity appears after the first check: no migration begins; stopped original
  application containers are restarted.
- Migration or later startup fails: application admission is stopped and data is
  retained. Inspect the failing service through your privileged operations
  channel. Do not publish raw logs or resolved Compose configuration; these may
  contain secrets.
- There is **no automatic image or database rollback**. A prior binary may not
  understand a schema that has already advanced. Use a compatible corrected
  release or an authorized database restore; do not edit migration checksums.

The package does not fetch `main`, change the Docker daemon proxy, provision the
OpenSandbox host, remove historical release directories, or clean data volumes.
Its running container/image identity is the deployment authority; legacy
source-checkout `subject` files are not updated or used by this entry.

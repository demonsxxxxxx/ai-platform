# Deployment guide

There is one current deployment package, `ai-platform-internal-test.tar.gz`, and
one [installation and upgrade guide](README.internal-test.md). The retained
`internal-test` archive/source filename is historical naming, not a second
package or a choice of deployment tracks. The assembler embeds that guide as
`README.md` in the archive. Download the package from one immutable Deployment
Release and follow its included instructions; do not assemble it from source
templates or mix files from different Releases.

The former standalone static-frontend deployment CLI is retired. Frontend
installation and upgrades use this same package and its image/provenance checks;
there is no separate static-directory deployment path.

See the [release operations runbook](../../docs/operations/release-operations-runbook.md)
for publication and acceptance. The current package still enforces the
`test` / `internal-test` configuration and Docker `bridge` / `runsc` prerequisites.
Unifying the instructions does not add production network isolation or authorize
changes to credentials, host configuration, data or permissions.

The [dedicated-network host contract](../../docs/operations/production-bootstrap.md)
is retained for controlled maintenance of hosts already using that topology;
it is not another package installation path. Existing immutable Releases and
runtime configuration templates are unchanged.

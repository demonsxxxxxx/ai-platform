# Release Evidence

This directory is copied into the backend image. Its only current reader is the
Admin Skill release review for `builtin` Skill versions
(`app/skills/release_readiness.py`, `build_skill_version_release_review`), which
reads [skill-release](skill-release/README.md) by default.

Entries contain no credentials, raw environment values, private executor
payloads, storage keys, sandbox work directories, or absolute private paths.
Ordinary pull requests, local tests, CI jobs, and review comments keep their
output in GitHub checks or Actions artifacts, not here. Deployment package
qualification evidence is generated into the package's own `release-evidence/`
by `tools/release_compose_package.py` and is not stored in this directory.

The Foundation Alpha, capacity, sandbox, Office context, governance and
observability gate evidence formerly kept here was removed together with its
offline readiness tools on 2026-10-24; it remains in Git history.

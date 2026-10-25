# Skill Release Evidence

Status: pending scaffolds read at runtime by the Admin Skill release review.

Each `<skill-id>/` directory holds the evidence that
`build_skill_version_release_review` in `app/skills/release_readiness.py` checks
for a `builtin` Skill version: an SBOM (`sbom.json`), license notices
(`third-party-notices.txt`), a vulnerability report (`vulnerability-report.json`)
and the review manifest `ai-platform-skill-release-review.json`. Uploaded Skill
versions use the uploaded-package trust review instead.

All current manifests are `pending`, so the review reports these versions as
blocked. A manifest may move from `pending` to `passed` only after an operator
verifies the matching SBOM or signed-package evidence, license policy evidence
and vulnerability evidence, then sets all review flags to `true`.

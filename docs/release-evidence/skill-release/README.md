# Skill Release Evidence Scaffold

This directory contains pending Skill release evidence scaffold files generated
from the repository skill inventory.

These files are not reviewed release evidence by themselves and do not close
G6. They provide source-bound SBOM, license-policy, vulnerability, and review
manifest inputs for operator review.

A skill release review manifest may move from `pending` to `passed` only after
an operator verifies the matching SBOM or signed-package evidence, license
policy evidence, and vulnerability evidence, then sets all review flags to
`true`.

Current readiness:

```powershell
python tools/skill_release_readiness.py --format json
```

Historical dependency-review runtime evidence is separate from these source
scaffolds. The governance inventory producer has been retired because Admin
Runtime exposes an offline reference rather than the old source inventory.
Do not run the old verifier or restore live source scans to create new evidence.

Existing reviewed, redacted entries under
`docs/release-evidence/skill-release-runtime/<runtime-subject>/` remain readable
under their recorded contract. The Foundation evidence wrapper retains the old
verifier identity only for packaging archived output; it does not collect or
refresh runtime observations. A current-runtime acceptance replacement requires
an owning runtime observation and its endpoint contract first. Removing the
producer does not close the dependency-review runtime gap or any release gate.

`tools/skill_release_readiness.py` accepts that evidence only when the wrapped
entry uses schema `ai-platform.release-evidence-entry.v1`, gate
`G6 Skill Release / Dependency Governance`, artifact kind
`skill_dependency_review_policy_runtime_acceptance`, verifier
`tools/verify_governance_runtime_smoke.py`, verifier schema
`ai-platform.governance-runtime-smoke.v1`, nested runtime payload schema
`ai-platform.skill-dependency-review-runtime-acceptance.v1`, reviewed status,
and passed redaction scan. Closing this runtime gap does not close G6, signed
package/SBOM review, dependency vulnerability/license review, or Admin Skill
release dashboard acceptance.

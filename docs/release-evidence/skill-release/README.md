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

Runtime acceptance evidence for the dependency-review policy is separate from
these source scaffolds. Operators should write reviewed, redacted 211 evidence
under `docs/release-evidence/skill-release-runtime/<runtime-subject>/` after
running the Admin Runtime governance verifier:

```powershell
python tools/verify_governance_runtime_smoke.py --base-url http://127.0.0.1:8020 --commit-sha <source-tree-commit> --runtime-subject-commit-sha <runtime-subject-commit> --image <runtime-image>
python tools/wrap_foundation_alpha_evidence.py --verifier-output <verifier-output.json> --verifier tools/verify_governance_runtime_smoke.py --artifact-kind skill_dependency_review_policy_runtime_acceptance --gate "G6 Skill Release / Dependency Governance" --evidence-id <evidence-id> --commit-sha <source-tree-commit> --runtime-subject-commit-sha <runtime-subject-commit> --image <runtime-image> --image-id <runtime-image-id> --image-labels-json <image-labels.json> --command "<redacted-verifier-command>" --review-status reviewed --output docs/release-evidence/skill-release-runtime/<runtime-subject>/<evidence-id>.json
```

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

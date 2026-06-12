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

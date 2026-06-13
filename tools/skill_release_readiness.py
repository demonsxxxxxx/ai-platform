import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.skills.release_readiness import (  # noqa: E402
    build_skill_release_readiness,
    build_skill_release_review_template,
    render_skill_release_readiness_markdown,
    render_skill_release_review_template_markdown,
    write_skill_release_evidence_scaffold,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the current ai-platform Skill release readiness baseline.")
    parser.add_argument(
        "--skills-root",
        default="skills",
        help="Skill inventory root to scan. Defaults to the repository skills directory.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format. Defaults to markdown.",
    )
    parser.add_argument(
        "--review-template",
        action="store_true",
        help="Print a pending skill release review manifest template instead of the readiness snapshot.",
    )
    parser.add_argument(
        "--evidence-root",
        default="docs/release-evidence/skill-release",
        help="Optional external Skill release evidence root. Defaults to docs/release-evidence/skill-release.",
    )
    parser.add_argument(
        "--write-evidence-scaffold",
        action="store_true",
        help="Write pending external release evidence inputs for --skill-id instead of the readiness snapshot.",
    )
    parser.add_argument(
        "--skill-id",
        help="Skill id for --review-template or --write-evidence-scaffold.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the rendered output. By default output is written to stdout only.",
    )
    args = parser.parse_args()

    if args.write_evidence_scaffold:
        if not args.skill_id:
            parser.error("--write-evidence-scaffold requires --skill-id")
        try:
            scaffold = write_skill_release_evidence_scaffold(
                skills_root=args.skills_root,
                evidence_root=args.evidence_root,
                skill_id=args.skill_id,
            )
        except (FileExistsError, ValueError) as exc:
            parser.error(str(exc))
        if args.format == "json":
            rendered = json.dumps(scaffold, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = (
                "# ai-platform Skill Release Evidence Scaffold\n\n"
                f"Schema: `{scaffold['schema_version']}`\n\n"
                f"Status: `{scaffold['status']}`\n\n"
                f"Skill: `{scaffold['skill_id']}`\n\n"
                "Written files:\n\n"
                + "\n".join(f"- `{path}`" for path in scaffold["written_files"])
                + "\n\n"
                f"Does not close gate by itself: `{scaffold['does_not_close_gate_by_itself']}`\n"
            )
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"{rendered}\n", encoding="utf-8")
            return
        print(rendered)
        return

    if args.review_template:
        if not args.skill_id:
            parser.error("--review-template requires --skill-id")
        try:
            template = build_skill_release_review_template(skill_id=args.skill_id)
        except ValueError as exc:
            parser.error(str(exc))
        if args.format == "json":
            rendered = json.dumps(template, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = render_skill_release_review_template_markdown(template)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"{rendered}\n", encoding="utf-8")
            return
        print(rendered)
        return

    readiness = build_skill_release_readiness(
        skills_root=args.skills_root,
        skill_release_evidence_root=args.evidence_root,
    )
    if args.format == "json":
        rendered = json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        rendered = render_skill_release_readiness_markdown(readiness)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{rendered}\n", encoding="utf-8")
        return
    print(rendered)


if __name__ == "__main__":
    main()

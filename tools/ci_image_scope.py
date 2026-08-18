from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Iterable, Sequence

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")

_EXACT_INPUTS = {
    "backend": frozenset(
        {
            ".dockerignore",
            ".gitattributes",
            ".github/workflows/ai-platform-backend.yml",
            "Dockerfile",
            "docker-entrypoint.sh",
            "pyproject.toml",
            "uv.lock",
        }
    ),
    "frontend": frozenset(
        {
            ".dockerignore",
            ".gitattributes",
            ".github/workflows/ai-platform-frontend.yml",
            "tests/test_frontend_linux_contracts.py",
        }
    ),
}

_PREFIX_INPUTS = {
    "backend": ("app/", "docs/release-evidence/", "scripts/", "skills/", "tools/"),
    "frontend": ("frontend/web/", "tools/"),
}


def image_inputs_affected(role: str, changed_paths: Iterable[str]) -> bool:
    exact = _EXACT_INPUTS[role]
    prefixes = _PREFIX_INPUTS[role]
    return any(path in exact or path.startswith(prefixes) for path in changed_paths)


def image_validation_disposition(
    *, event_name: str, role: str, changed_paths: Iterable[str]
) -> tuple[bool, str]:
    if event_name != "pull_request":
        return True, "required_event"
    if image_inputs_affected(role, changed_paths):
        return True, "affected"
    return False, "not_affected"


def changed_paths(base_ref: str, head_ref: str) -> tuple[str, ...]:
    for label, value in (("base", base_ref), ("head", head_ref)):
        if _COMMIT_SHA.fullmatch(value) is None:
            raise ValueError(f"{label} ref must be an exact lowercase commit SHA")
        subprocess.run(
            ["git", "cat-file", "-e", f"{value}^{{commit}}"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_ref, head_ref],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base_ref, head_ref],
        check=True,
        stdout=subprocess.PIPE,
    )
    return tuple(
        raw_path.decode("utf-8")
        for raw_path in completed.stdout.split(b"\0")
        if raw_path
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decide whether a pull request changes packaged image inputs."
    )
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--role", choices=sorted(_EXACT_INPUTS), required=True)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head-ref", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths: tuple[str, ...] = ()
    if args.event_name == "pull_request":
        paths = changed_paths(args.base_ref, args.head_ref)
    build, disposition = image_validation_disposition(
        event_name=args.event_name,
        role=args.role,
        changed_paths=paths,
    )
    print(f"build={'true' if build else 'false'}")
    print(f"disposition={disposition}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

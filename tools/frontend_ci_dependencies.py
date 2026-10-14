"""Install the frontend CI's lightweight Python subset from the project lock."""
from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib


SUBSETS = {
    "windows-contracts": ("pytest", "pyyaml"),
    "linux-contracts": ("pytest",),
}
ROOT = Path(__file__).resolve().parents[1]


def install_dependencies(subset: str, *, root: Path = ROOT) -> None:
    names = SUBSETS[subset]
    with (root / "uv.lock").open("rb") as handle:
        packages = tomllib.load(handle)["package"]
    expected = {}
    for name in names:
        matches = [package for package in packages if package.get("name") == name]
        if len(matches) != 1 or not isinstance(matches[0].get("version"), str):
            raise RuntimeError(f"uv.lock must contain exactly one version for {name}")
        expected[name] = matches[0]["version"]

    # uv owns marker handling and lock freshness; pip installs only the requested
    # subset and its platform-specific dependencies, constrained by that export.
    with tempfile.TemporaryDirectory(prefix="frontend-ci-dependencies-") as directory:
        constraints = Path(directory) / "constraints.txt"
        subprocess.run(
            [
                "uv", "export", "--locked", "--extra", "test",
                "--no-emit-project", "--no-hashes", "--output-file", str(constraints),
            ],
            cwd=root, check=True, stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--constraint", str(constraints), *names],
            cwd=root, check=True,
        )
    for name, version in expected.items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(f"installed {name} distribution is missing") from error
        if installed != version:
            raise RuntimeError(f"installed {name} version differs from uv.lock")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", choices=SUBSETS, required=True)
    args = parser.parse_args()
    install_dependencies(args.subset)


if __name__ == "__main__":
    main()

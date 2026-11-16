#!/usr/bin/env python3
"""Check changed first-party documentation links."""

from __future__ import annotations

import argparse
from collections import Counter
import html
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


ENTRYPOINTS = {"AGENTS.md", "CLAUDE.md", "README.md", "ARCHITECTURE.md", "docs/README.md"}
HISTORICAL_ROOTS = ("docs/release-evidence/", "docs/agent-rules/history/", "docs/exec-plans/completed/")


def historical(path: str) -> bool:
    return path.startswith(HISTORICAL_ROOTS)


def markdown_facts(text: str) -> tuple[list[tuple[int, str]], set[str]]:
    """Parse Markdown instead of treating examples/code as actual links."""
    tokens = MarkdownIt("commonmark", {"html": True}).parse(text)
    links = []
    anchors = set()
    counts = Counter()
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            heading = "".join(child.content for child in inline.children or [] if child.type in {"text", "code_inline"})
            slug = re.sub(r"[^\w\- ]", "", html.unescape(heading).lower()).replace(" ", "-")
            anchor = slug if counts[slug] == 0 else f"{slug}-{counts[slug]}"
            while anchor in anchors:
                counts[slug] += 1
                anchor = f"{slug}-{counts[slug]}"
            anchors.add(anchor)
            counts[slug] += 1
        for child in token.children or []:
            if child.type in {"link_open", "image"}:
                links.append(((token.map or [0])[0] + 1, child.attrGet("href" if child.type == "link_open" else "src") or ""))
        if token.type in {"html_block", "inline"}:
            raw_html = token.content if token.type == "html_block" else " ".join(child.content for child in token.children or [] if child.type == "html_inline")
            anchors.update(re.findall(r"\b(?:id|name)=[\"']([^\"']+)[\"']", raw_html))
    return links, anchors


def local_target(root: Path, document: Path, href: str) -> tuple[Path, str] | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    path = re.sub(r":\d+$", "", path)
    target = (root / path.lstrip("/") if path.startswith("/") else document.parent / path).resolve() if path else document
    return target, unquote(parsed.fragment)


def check_document_links(
    root: Path, document: Path, *, check_anchors: bool = True, require_files: bool = False,
) -> tuple[list[str], set[Path]]:
    """Share rendered-link validation with the authority-map regression.

    Authority owners require file targets; ordinary Markdown may link directories.
    Anchor validation is opt-in for that existing file-link-only contract.
    """
    root, document = root.resolve(), document.resolve()
    relative = document.relative_to(root).as_posix()
    findings, targets = [], set()
    links, _ = markdown_facts(document.read_text(encoding="utf-8"))
    for line, href in links:
        target = local_target(root, document, href)
        if target is None:
            continue
        path, anchor = target
        if not path.is_relative_to(root):
            findings.append(f"{relative}:{line}: link_outside_repository: use a fixed external history reference or a repository target")
        elif not path.exists() or (require_files and not path.is_file()):
            findings.append(f"{relative}:{line}: link_target_missing: repair or retire the link to {href}")
        else:
            targets.add(path)
            if check_anchors and anchor and path.suffix == ".md" and path.is_file():
                _, anchors = markdown_facts(path.read_text(encoding="utf-8"))
                if anchor not in anchors:
                    findings.append(f"{relative}:{line}: link_anchor_missing: repair the heading reference {href}")
    return findings, targets


def check_documents(root: Path, paths: list[str]) -> tuple[list[str], list[str]]:
    """Return actionable findings and explicitly excluded snapshot/history paths."""
    root = root.resolve()
    findings = []
    excluded = []
    for relative in sorted(set(paths)):
        document = (root / relative).resolve()
        if not document.is_relative_to(root) or document.suffix != ".md" or not (relative in ENTRYPOINTS or relative.startswith("docs/")):
            findings.append(f"{relative}: document_path_invalid: select repository entrypoints or docs/*.md")
            continue
        if historical(relative):
            excluded.append(relative)
            continue
        if not document.is_file():
            findings.append(f"{relative}: document_missing: restore the document or update its consumers")
            continue
        link_findings, _ = check_document_links(root, document)
        findings.extend(link_findings)
    return findings, excluded


def changed_documents(root: Path, base_ref: str | None = None) -> list[str]:
    if base_ref is not None and not re.fullmatch(r"[0-9a-f]{40}", base_ref):
        raise ValueError("base ref must be a full commit SHA")
    paths = set()
    base = subprocess.check_output(
        ["git", "merge-base", base_ref, "HEAD"], cwd=root, timeout=15,
    ).decode("utf-8").strip() if base_ref else "HEAD"
    for arguments in (("diff", base, "--name-only", "-z"), ("ls-files", "--others", "--exclude-standard", "-z")):
        paths.update(subprocess.check_output(["git", *arguments], cwd=root, timeout=15).decode("utf-8").split("\0"))
    return sorted(path for path in paths if path.endswith(".md") and (path in ENTRYPOINTS or path.startswith("docs/")) and (root / path).is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="full comparison commit for CI; default HEAD for local uncommitted changes")
    parser.add_argument("documents", nargs="*", help="explicit worktree-relative documents; defaults to changed tracked/untracked documents")
    arguments = parser.parse_args()
    try:
        root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, timeout=15).strip())
        if arguments.documents and arguments.base_ref:
            raise ValueError("choose explicit documents or a base comparison")
        paths = arguments.documents or changed_documents(root, arguments.base_ref)
        findings, excluded = check_documents(root, paths)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"documentation_check_unavailable: {type(error).__name__}; prepare the locked environment and run from the target worktree")
        return 2
    for finding in findings:
        print(finding)
    for path in excluded:
        print(f"historical_reference_excluded: {path}; preserve its fixed-source provenance")
    print(f"documentation_check: {len(paths) - len(excluded)} selected, {len(excluded)} historical/snapshot, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

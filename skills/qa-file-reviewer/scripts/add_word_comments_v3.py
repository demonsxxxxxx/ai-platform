#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Document Reviewer - Word Comment Writer

将审核 JSON 写入 Word 批注，保留源文档已有人工批注，并剥离旧的自动审核批注。

Usage:
    python scripts/add_word_comments_v3.py <input_docx> <review_json> <output_docx>

Optional:
    python scripts/add_word_comments_v3.py <input_docx> <review_json> <output_docx> --author 文件审核系统
"""

from __future__ import annotations

import copy
import argparse
import json
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree


WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XML_SPACE_ATTR = f"{{{XML_NS}}}space"
NS = {"w": WML_NS, "r": REL_NS, "ct": CT_NS}

W = f"{{{WML_NS}}}"
R = f"{{{REL_NS}}}"
CT = f"{{{CT_NS}}}"

AUTOMATED_COMMENT_AUTHORS = {
    "QA审核系统",
    "文件审核系统",
    "Codex Document Reviewer",
    "Document Reviewer",
}

IGNORABLE_PREFIX_NAMESPACE_URIS = {
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16cid": "http://schemas.microsoft.com/office/word/2016/wordml/cid",
    "w16se": "http://schemas.microsoft.com/office/word/2015/wordml/symex",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
}

etree.register_namespace("w", WML_NS)
etree.register_namespace("r", REL_NS)


ANCHOR_CHAR_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "＂": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "＇": "'",
    }
)


def normalize_anchor_char(char: str) -> str:
    return char.translate(ANCHOR_CHAR_TRANSLATION)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write review comments into a DOCX while preserving human comments."
    )
    parser.add_argument("input_docx")
    parser.add_argument("review_json")
    parser.add_argument("output_docx")
    parser.add_argument("--author", default="文件审核系统")
    parser.add_argument(
        "--preserve-automated-comments",
        action="store_true",
        help="Keep prior automated reviewer comments instead of stripping them before this run.",
    )
    return parser.parse_args()


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", "", text.translate(ANCHOR_CHAR_TRANSLATION))


def paragraph_text(paragraph: etree._Element) -> str:
    return "".join(text.text or "" for text in paragraph.findall(".//w:t", NS))


def paragraph_runs(paragraph: etree._Element) -> List[etree._Element]:
    return paragraph.findall(".//w:r", NS)


def direct_paragraph_runs(paragraph: etree._Element) -> List[etree._Element]:
    runs: List[etree._Element] = []
    for child in list(paragraph):
        if child.tag == f"{{{WML_NS}}}r":
            runs.append(child)
        elif child.tag == f"{{{WML_NS}}}hyperlink":
            runs.extend(child.findall("w:r", NS))
    return runs


def direct_run_text(run: etree._Element) -> str:
    return "".join(text.text or "" for text in run.findall(".//w:t", NS))


def build_direct_run_segments(paragraph: etree._Element) -> Tuple[List[Dict[str, Any]], str]:
    segments: List[Dict[str, Any]] = []
    cursor = 0

    for run in direct_paragraph_runs(paragraph):
        text = direct_run_text(run)
        if not text:
            continue
        start = cursor
        end = cursor + len(text)
        segments.append(
            {
                "run": run,
                "text": text,
                "start": start,
                "end": end,
            }
        )
        cursor = end

    return segments, "".join(segment["text"] for segment in segments)


def normalize_text_with_map(text: str) -> Tuple[str, List[int]]:
    normalized_chars: List[str] = []
    mapping: List[int] = []

    for index, char in enumerate(text):
        if char.isspace():
            continue
        normalized_chars.append(normalize_anchor_char(char))
        mapping.append(index)

    return "".join(normalized_chars), mapping


def extract_anchor_span(issue: Dict[str, Any]) -> Optional[Dict[str, int | str]]:
    raw_span = issue.get("anchor_span")
    if raw_span is None:
        return None

    start: Any
    end: Any
    unit = "char"

    if isinstance(raw_span, dict):
        start = raw_span.get("start")
        end = raw_span.get("end")
        unit = str(raw_span.get("unit") or "char").strip().lower() or "char"
    elif isinstance(raw_span, (list, tuple)) and len(raw_span) >= 2:
        start, end = raw_span[0], raw_span[1]
    else:
        return None

    if unit != "char":
        return None

    try:
        start_int = int(start)
        end_int = int(end)
    except (TypeError, ValueError):
        return None

    if start_int < 0 or end_int <= start_int:
        return None

    return {"start": start_int, "end": end_int, "unit": unit}


def issue_anchor_length(issue: Dict[str, Any]) -> int:
    span = extract_anchor_span(issue)
    if span is not None:
        return int(span["end"]) - int(span["start"])
    original = str(issue.get("original") or issue.get("anchor_text") or issue.get("issue") or "")
    return max(len(original), 9999)


def comment_order_key(issue: Dict[str, Any]) -> Tuple[int, int, int, int]:
    append_last = 1 if issue.get("append_to_document_end") else 0
    status = str(issue.get("status") or "")
    needs_check_last = 1 if status == "needs_user_check" else 0
    return (
        append_last,
        issue_anchor_length(issue),
        needs_check_last,
        len(str(issue.get("issue") or "")),
    )


def anchor_span_matches_issue(source_text: str, issue: Dict[str, Any], span: Dict[str, int | str]) -> bool:
    start = int(span["start"])
    end = int(span["end"])
    matched = source_text[start:end]
    expected_values = [
        str(issue.get("anchor_text") or "").strip(),
        str(issue.get("original") or "").strip(),
    ]
    matched_clean = normalize_text(matched).lower()
    for expected in expected_values:
        expected_clean = normalize_text(expected).lower()
        if len(expected_clean) < 2:
            continue
        if expected_clean in matched_clean or matched_clean in expected_clean:
            return True
    return False


def clone_run_with_text(template_run: etree._Element, text: str) -> etree._Element:
    run = etree.Element(f"{{{WML_NS}}}r")
    for key, value in template_run.attrib.items():
        run.set(key, value)

    rpr = template_run.find("w:rPr", NS)
    if rpr is not None:
        run.append(copy.deepcopy(rpr))

    t = etree.SubElement(run, f"{{{WML_NS}}}t")
    if text.startswith(" ") or text.endswith(" "):
        t.set(XML_SPACE_ATTR, "preserve")
    t.text = text
    return run


def split_run_at_offset(run: etree._Element, offset: int) -> Tuple[Optional[etree._Element], Optional[etree._Element]]:
    text = direct_run_text(run)
    if offset <= 0:
        return None, run
    if offset >= len(text):
        return run, None

    parent = run.getparent()
    if parent is None:
        raise ValueError("run has no parent paragraph")

    left = clone_run_with_text(run, text[:offset])
    right = clone_run_with_text(run, text[offset:])

    children = list(parent)
    run_index = children.index(run)
    parent.remove(run)
    parent.insert(run_index, left)
    parent.insert(run_index + 1, right)
    return left, right


def candidate_run_ids(segments: List[Dict[str, Any]], start: int, end: int) -> List[int]:
    ids: List[int] = []
    for segment in segments:
        if segment["end"] <= start or segment["start"] >= end:
            continue
        ids.append(id(segment["run"]))
    return ids


def span_conflicts(
    paragraph: etree._Element,
    start: int,
    end: int,
    used_spans_by_paragraph: Dict[int, List[Tuple[int, int]]],
) -> bool:
    existing = used_spans_by_paragraph.get(id(paragraph), [])
    for used_start, used_end in existing:
        if start == used_start and end == used_end:
            continue
        if not (end <= used_start or start >= used_end):
            return True
    return False


def find_text_spans(source_text: str, needle: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    raw_needle = (needle or "").strip()
    if not raw_needle:
        return candidates

    start = source_text.find(raw_needle)
    while start != -1:
        candidates.append(
            {
                "start": start,
                "end": start + len(raw_needle),
                "match_method": "exact",
            }
        )
        start = source_text.find(raw_needle, start + 1)

    if candidates:
        return candidates

    normalized_source, mapping = normalize_text_with_map(source_text)
    normalized_needle, _ = normalize_text_with_map(raw_needle)
    if len(normalized_needle) < 2 or not normalized_source:
        return candidates

    start = normalized_source.find(normalized_needle)
    while start != -1:
        raw_start = mapping[start]
        raw_end = mapping[start + len(normalized_needle) - 1] + 1
        candidates.append(
            {
                "start": raw_start,
                "end": raw_end,
                "match_method": "contains",
            }
        )
        start = normalized_source.find(normalized_needle, start + 1)

    return candidates


def quoted_anchor_needles(text: str) -> List[str]:
    needles: List[str] = []
    seen: set[str] = set()
    for quoted in re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,80})[\"'“”‘’]", text or ""):
        needle = quoted.strip()
        normalized = normalize_text(needle)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        needles.append(needle)
    return needles


def all_paragraphs(root: etree._Element) -> List[etree._Element]:
    return root.findall(".//w:p", NS)


def extract_paragraph_number(*values: Optional[str]) -> Optional[int]:
    xml_patterns = [
        r"\bxml(?:_index)?\s*[:：=]\s*(\d+)",
        r"\bparagraph_id\s*[:：=]\s*p-0*(\d+)",
        r"\bp-0*(\d+)\b",
    ]
    patterns = [
        r"paragraph\s*=?\s*(\d+)",
        r"para\.?\s*(\d+)",
        r"段落\s*(\d+)",
        r"第\s*(\d+)\s*段",
        r"(?<![A-Za-z])p\s*(\d+)(?!\d)",
        r"(?<![A-Za-z])P\s*(\d+)(?!\d)",
    ]

    for value in values:
        if not value:
            continue
        text = str(value)
        for pattern in xml_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
    return None


def parse_xml_bytes(data: bytes) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, recover=False, huge_tree=True)
    return etree.ElementTree(etree.fromstring(data, parser=parser))


def tree_to_bytes(tree: etree._ElementTree) -> bytes:
    return etree.tostring(
        tree.getroot(),
        encoding="utf-8",
        xml_declaration=True,
        standalone=True,
    )


def missing_ignorable_prefix_declarations(tree: etree._ElementTree) -> Dict[str, str]:
    root = tree.getroot()
    ignorable = root.get(f"{{{MC_NS}}}Ignorable", "")
    return {
        prefix: IGNORABLE_PREFIX_NAMESPACE_URIS[prefix]
        for prefix in ignorable.split()
        if prefix not in root.nsmap and prefix in IGNORABLE_PREFIX_NAMESPACE_URIS
    }


def inject_namespace_declarations(xml_bytes: bytes, declarations: Dict[str, str]) -> bytes:
    if not declarations:
        return xml_bytes

    search_start = 0
    if xml_bytes.startswith(b"<?xml"):
        declaration_end = xml_bytes.find(b"?>")
        if declaration_end != -1:
            search_start = declaration_end + 2

    root_start = xml_bytes.find(b"<", search_start)
    root_end = xml_bytes.find(b">", root_start)
    if root_start == -1 or root_end == -1:
        return xml_bytes

    attrs = "".join(f' xmlns:{prefix}="{uri}"' for prefix, uri in declarations.items())
    return xml_bytes[:root_end] + attrs.encode("utf-8") + xml_bytes[root_end:]


def document_tree_to_bytes(tree: etree._ElementTree) -> bytes:
    return inject_namespace_declarations(
        tree_to_bytes(tree),
        missing_ignorable_prefix_declarations(tree),
    )


def existing_comment_ids(comments_root: etree._Element) -> List[int]:
    ids: List[int] = []
    for comment in comments_root.findall(".//w:comment", NS):
        raw = comment.get(f"{W}id")
        if raw is None:
            continue
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def automated_comment_ids(comments_root: etree._Element) -> set[str]:
    ids: set[str] = set()
    for comment in comments_root.findall(".//w:comment", NS):
        author = (comment.get(f"{W}author") or "").strip()
        raw_id = comment.get(f"{W}id")
        if raw_id is not None and author in AUTOMATED_COMMENT_AUTHORS:
            ids.add(str(raw_id))
    return ids


def remove_nodes_by_comment_id(root: etree._Element, ids: set[str]) -> int:
    removed = 0
    if not ids:
        return removed
    for tag in ("commentRangeStart", "commentRangeEnd", "commentReference"):
        for node in list(root.findall(f".//w:{tag}", NS)):
            raw_id = node.get(f"{W}id")
            if raw_id not in ids:
                continue
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
                removed += 1
    return removed


def strip_prior_automated_comments(
    document_root: etree._Element, comments_root: etree._Element
) -> int:
    ids = automated_comment_ids(comments_root)
    if not ids:
        return 0

    removed_defs = 0
    for comment in list(comments_root.findall(".//w:comment", NS)):
        raw_id = comment.get(f"{W}id")
        if raw_id not in ids:
            continue
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
            removed_defs += 1

    remove_nodes_by_comment_id(document_root, ids)
    return removed_defs


def next_comment_id(comments_root: etree._Element) -> int:
    ids = existing_comment_ids(comments_root)
    return max(ids) + 1 if ids else 0


def ensure_comments_part(
    rels_tree: etree._ElementTree, ct_tree: etree._ElementTree, comments_root: etree._Element
) -> None:
    rels_root = rels_tree.getroot()

    existing = [
        rel
        for rel in rels_root.findall(".//r:Relationship", NS)
        if rel.get("Type")
        == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
    ]
    if not existing:
        ids = []
        for rel in rels_root.findall(".//r:Relationship", NS):
            rid = rel.get("Id", "")
            match = re.search(r"rId(\d+)", rid)
            if match:
                ids.append(int(match.group(1)))
        new_id = f"rId{max(ids) + 1 if ids else 1}"
        rel = etree.Element(f"{{{REL_NS}}}Relationship")
        rel.set("Id", new_id)
        rel.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
        )
        rel.set("Target", "comments.xml")
        rels_root.append(rel)

    ct_root = ct_tree.getroot()
    existing_override = [
        item
        for item in ct_root.findall(".//ct:Override", {"ct": CT_NS})
        if item.get("PartName") == "/word/comments.xml"
    ]
    if not existing_override:
        override = etree.Element(f"{{{CT_NS}}}Override")
        override.set("PartName", "/word/comments.xml")
        override.set(
            "ContentType",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        )
        ct_root.append(override)


def validate_output_docx(docx_path: str) -> Dict[str, Any]:
    """
    对生成后的 DOCX 做有效性校验，重点检查命名空间前缀和批注引用一致性。
    """
    result: Dict[str, Any] = {
        "valid": False,
        "errors": [],
        "warnings": [],
        "document_comment_starts": 0,
        "document_comment_ends": 0,
        "document_comment_refs": 0,
        "comment_definitions": 0,
    }

    with zipfile.ZipFile(docx_path, "r") as zip_ref:
        entries = {info.filename: zip_ref.read(info.filename) for info in zip_ref.infolist() if not info.is_dir()}

    required_parts = [
        "word/document.xml",
        "word/comments.xml",
        "word/_rels/document.xml.rels",
        "[Content_Types].xml",
    ]
    for part in required_parts:
        if part not in entries:
            result["errors"].append(f"缺少必要部件: {part}")
            return result

    document_tree = parse_xml_bytes(entries["word/document.xml"])
    comments_tree = parse_xml_bytes(entries["word/comments.xml"])
    rels_tree = parse_xml_bytes(entries["word/_rels/document.xml.rels"])
    ct_tree = parse_xml_bytes(entries["[Content_Types].xml"])

    document_root = document_tree.getroot()
    comments_root = comments_tree.getroot()
    rels_root = rels_tree.getroot()
    ct_root = ct_tree.getroot()

    ignorable = document_root.get(f"{{{MC_NS}}}Ignorable", "")
    missing_prefixes = [prefix for prefix in ignorable.split() if prefix and prefix not in document_root.nsmap]
    if missing_prefixes:
        result["errors"].append(
            "document.xml 根节点的 mc:Ignorable 引用了未声明前缀: " + ", ".join(missing_prefixes)
        )

    comment_starts = document_root.findall(".//w:commentRangeStart", NS)
    comment_ends = document_root.findall(".//w:commentRangeEnd", NS)
    comment_refs = document_root.findall(".//w:commentReference", NS)
    comment_defs = comments_root.findall(".//w:comment", NS)

    result["document_comment_starts"] = len(comment_starts)
    result["document_comment_ends"] = len(comment_ends)
    result["document_comment_refs"] = len(comment_refs)
    result["comment_definitions"] = len(comment_defs)

    if not (len(comment_starts) == len(comment_ends) == len(comment_refs) == len(comment_defs)):
        result["errors"].append(
            "批注标记数量不一致: "
            f"start={len(comment_starts)}, end={len(comment_ends)}, ref={len(comment_refs)}, defs={len(comment_defs)}"
        )

    comments_rel = [
        rel
        for rel in rels_root.findall(".//r:Relationship", NS)
        if rel.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
    ]
    if not comments_rel:
        result["errors"].append("document.xml.rels 中缺少 comments 关系")

    comments_override = [
        item
        for item in ct_root.findall(".//ct:Override", {"ct": CT_NS})
        if item.get("PartName") == "/word/comments.xml"
    ]
    if not comments_override:
        result["errors"].append("[Content_Types].xml 中缺少 comments.xml Override")

    result["valid"] = not result["errors"]
    return result


def find_paragraph_by_number(
    paragraphs: List[etree._Element],
    paragraph_number: int,
    expected_text: str = "",
) -> Optional[etree._Element]:
    if paragraph_number < 1 or paragraph_number > len(paragraphs):
        return None

    paragraph = paragraphs[paragraph_number - 1]
    if expected_text and len(normalize_text(expected_text)) >= 2:
        para_clean = normalize_text(paragraph_text(paragraph))
        expected_clean = normalize_text(expected_text)
        if expected_clean not in para_clean and expected_clean.lower() not in para_clean.lower():
            return None
    return paragraph


def search_paragraph_by_text(
    paragraphs: List[etree._Element], search_text: str, location_hint: str = ""
) -> Optional[etree._Element]:
    clean = normalize_text(search_text)
    if len(clean) < 3:
        return None

    terms: List[str] = []
    if len(clean) > 80:
        mid = len(clean) // 2
        terms.extend([clean[mid - 30 : mid + 30], clean[:40], clean[-40:]])
    elif len(clean) > 40:
        mid = len(clean) // 2
        terms.extend([clean[mid - 20 : mid + 20], clean[:30], clean[-30:]])
    elif len(clean) > 16:
        terms.extend([clean[:24], clean[-24:], clean])
    else:
        terms.append(clean)

    terms = [term for term in terms if term]
    if not terms:
        return None

    location_para = extract_paragraph_number(location_hint)
    matches: List[Tuple[int, etree._Element]] = []
    paragraphs_all = list(paragraphs)

    for index, paragraph in enumerate(paragraphs_all, start=1):
        para_clean = normalize_text(paragraph_text(paragraph))
        if any(term in para_clean for term in terms):
            matches.append((index, paragraph))

    if not matches:
        return None

    if location_para:
        matches.sort(key=lambda item: abs(item[0] - location_para))
    return matches[0][1]


def is_strict_agent_anchor_issue(issue: Dict[str, Any]) -> bool:
    if issue.get("unit_id"):
        return True
    if str(issue.get("branch") or "") == "llm_full_review":
        return True
    source = str(issue.get("source") or "").casefold()
    return "agent-review" in source or source == "agent"


def resolve_target_paragraph(
    paragraphs: List[etree._Element], issue: Dict[str, Any]
) -> Tuple[Optional[etree._Element], Dict[str, Any]]:
    anchor_locator = issue.get("anchor_locator", "")
    location = issue.get("location", "")
    original = issue.get("anchor_text") or issue.get("original") or ""
    evidence = issue.get("evidence") or ""

    paragraph_number = extract_paragraph_number(anchor_locator, location)
    if paragraph_number:
        paragraph = find_paragraph_by_number(paragraphs, paragraph_number, original)
        if paragraph is not None:
            return paragraph, {
                "strategy": "paragraph",
                "confidence": "high",
                "paragraph_number": paragraph_number,
            }

    if is_strict_agent_anchor_issue(issue):
        return None, {
            "strategy": "needs_human_review",
            "confidence": "low",
            "reason": "strict agent anchor did not match the target paragraph",
            "paragraph_number": paragraph_number,
        }

    if original:
        paragraph = search_paragraph_by_text(paragraphs, original, location)
        if paragraph is not None:
            return paragraph, {
                "strategy": "text_search",
                "confidence": "medium",
                "paragraph_number": None,
            }

    if evidence and evidence != original:
        paragraph = search_paragraph_by_text(paragraphs, evidence, location)
        if paragraph is not None:
            return paragraph, {
                "strategy": "evidence_search",
                "confidence": "medium",
                "paragraph_number": None,
            }

    if paragraph_number:
        paragraph = find_paragraph_by_number(paragraphs, paragraph_number)
        if paragraph is not None:
            return paragraph, {
                "strategy": "paragraph_fallback",
                "confidence": "low",
                "paragraph_number": paragraph_number,
            }

    return None, {
        "strategy": "needs_human_review",
        "confidence": "low",
        "reason": "no stable anchor; require human review",
    }


def select_anchor_candidate(
    paragraph: etree._Element,
    issue: Dict[str, Any],
    used_spans_by_paragraph: Dict[int, List[Tuple[int, int]]],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    segments, source_text = build_direct_run_segments(paragraph)
    if not segments:
        reason = "paragraph text is inside unsupported XML containers"
        if not normalize_text(paragraph_text(paragraph)):
            reason = "paragraph has no text runs"
        return None, {"reason": reason}

    candidates: List[Dict[str, Any]] = []
    seen: set[Tuple[int, int, str]] = set()

    explicit_span = extract_anchor_span(issue)
    if explicit_span is not None:
        if not anchor_span_matches_issue(source_text, issue, explicit_span):
            explicit_span = None
        else:
            candidate = {
                "start": explicit_span["start"],
                "end": explicit_span["end"],
                "match_method": "span",
                "anchor_source": "anchor_span",
            }
            run_ids = candidate_run_ids(segments, candidate["start"], candidate["end"])
            if run_ids and not span_conflicts(paragraph, candidate["start"], candidate["end"], used_spans_by_paragraph):
                candidate["run_ids"] = run_ids
                candidate["matched_text"] = source_text[candidate["start"] : candidate["end"]]
                candidate["ambiguous"] = False
                candidate["confidence"] = "high"

            span_key = (candidate["start"], candidate["end"], "span")
            if span_key not in seen:
                seen.add(span_key)
                candidates.append(candidate)

    if not is_strict_agent_anchor_issue(issue):
        for source_key in ("anchor_text", "original", "evidence"):
            needle = issue.get(source_key) or ""
            if not needle:
                continue
            if source_key == "evidence":
                anchor_text = issue.get("anchor_text") or issue.get("original") or ""
                if needle == anchor_text:
                    continue

            for candidate in find_text_spans(source_text, str(needle)):
                span_key = (candidate["start"], candidate["end"], candidate["match_method"])
                if span_key in seen:
                    continue
                seen.add(span_key)
                candidates.append(
                    {
                        "start": candidate["start"],
                        "end": candidate["end"],
                        "match_method": candidate["match_method"],
                        "anchor_source": source_key,
                    }
                )

        for source_key in ("issue", "suggestion"):
            for needle in quoted_anchor_needles(str(issue.get(source_key) or "")):
                for candidate in find_text_spans(source_text, needle):
                    span_key = (candidate["start"], candidate["end"], candidate["match_method"])
                    if span_key in seen:
                        continue
                    seen.add(span_key)
                    candidates.append(
                        {
                            "start": candidate["start"],
                            "end": candidate["end"],
                            "match_method": candidate["match_method"],
                            "anchor_source": source_key,
                        }
                    )

    valid_candidates: List[Dict[str, Any]] = []
    for candidate in candidates:
        run_ids = candidate_run_ids(segments, candidate["start"], candidate["end"])
        if not run_ids:
            continue
        if span_conflicts(paragraph, candidate["start"], candidate["end"], used_spans_by_paragraph):
            continue
        item = dict(candidate)
        item["run_ids"] = run_ids
        item["matched_text"] = source_text[candidate["start"] : candidate["end"]]
        valid_candidates.append(item)

    if not valid_candidates:
        return None, {
            "reason": "no stable anchor matched the current paragraph",
            "segments": segments,
            "source_text": source_text,
        }

    source_rank = {
        "anchor_span": 0,
        "evidence": 1,
        "suggestion": 2,
        "issue": 3,
        "anchor_text": 4,
        "original": 5,
    }
    match_rank = {"span": 0, "exact": 1, "contains": 2}
    def candidate_sort_key(item: Dict[str, Any]) -> Tuple[int, int, int, int]:
        width = int(item["end"]) - int(item["start"])
        source = str(item.get("anchor_source") or "")
        # A pre-adjudicated span is safer than a shorter quote fragment
        # extracted from explanatory issue text. Only broad paragraph-like spans
        # lose to specific evidence/suggestion snippets.
        broad_adjudicated_span = source == "anchor_span" and width > max(140, int(len(source_text) * 0.65))
        adjudicated_span = source == "anchor_span" and not broad_adjudicated_span
        return (
            0 if adjudicated_span else 1,
            width,
            match_rank.get(str(item.get("match_method") or ""), 9),
            source_rank.get(source, 9),
        )

    valid_candidates.sort(key=candidate_sort_key)
    chosen = valid_candidates[0]
    chosen["ambiguous"] = len(valid_candidates) > 1
    chosen["confidence"] = "high" if chosen["match_method"] in {"span", "exact"} else "medium"
    return chosen, {"segments": segments, "source_text": source_text}


def is_high_risk_ambiguous_anchor(meta: Dict[str, Any]) -> bool:
    """Explicit precomputed spans are allowed to have duplicate text candidates.

    Ambiguity becomes high risk when placement is selected from fuzzy or
    explanatory fields instead of the adjudicated anchor span. A sufficiently
    long exact quote remains stable within the already-selected paragraph.
    """

    if not meta.get("ambiguous"):
        return False
    anchor_source = str(meta.get("anchor_source") or "")
    match_method = str(meta.get("match_method") or "")
    if anchor_source == "anchor_span" and match_method == "span":
        return False
    matched_text = str(meta.get("matched_text") or "").strip()
    if match_method in {"span", "exact"} and len(matched_text) >= 5:
        return False
    return True


def materialize_anchor_span(
    paragraph: etree._Element,
    candidate: Dict[str, Any],
    segments: List[Dict[str, Any]],
) -> Tuple[Optional[etree._Element], Optional[etree._Element]]:
    start = int(candidate["start"])
    end = int(candidate["end"])

    start_item = None
    end_item = None
    for segment in segments:
        if segment["start"] <= start < segment["end"]:
            start_item = segment
        if segment["start"] < end <= segment["end"]:
            end_item = segment
        if start_item is not None and end_item is not None:
            break

    if start_item is None or end_item is None:
        return None, None

    if start_item["run"] is end_item["run"]:
        run = start_item["run"]
        end_offset = end - start_item["start"]
        left, right = split_run_at_offset(run, end_offset)
        working = left if left is not None else right
        if working is None:
            return None, None

        start_offset = start - start_item["start"]
        if start_offset > 0:
            _, middle = split_run_at_offset(working, start_offset)
            if middle is not None:
                working = middle
        return working, working

    end_offset = end - end_item["start"]
    if 0 < end_offset < len(end_item["text"]):
        left, _ = split_run_at_offset(end_item["run"], end_offset)
        end_run = left if left is not None else end_item["run"]
    else:
        end_run = end_item["run"]

    start_offset = start - start_item["start"]
    if 0 < start_offset < len(start_item["text"]):
        _, right = split_run_at_offset(start_item["run"], start_offset)
        start_run = right if right is not None else start_item["run"]
    else:
        start_run = start_item["run"]

    return start_run, end_run


def resolve_target_range(
    paragraphs: List[etree._Element],
    issue: Dict[str, Any],
    used_spans_by_paragraph: Dict[int, List[Tuple[int, int]]],
) -> Tuple[Optional[Tuple[etree._Element, etree._Element]], Dict[str, Any]]:
    paragraph, paragraph_meta = resolve_target_paragraph(paragraphs, issue)
    if paragraph is None:
        return None, paragraph_meta

    candidate, candidate_meta = select_anchor_candidate(paragraph, issue, used_spans_by_paragraph)
    if candidate is None:
        return None, {
            "strategy": "needs_human_review",
            "confidence": "low",
            "reason": candidate_meta.get("reason", "no stable anchor; require human review"),
            "paragraph_number": paragraph_meta.get("paragraph_number"),
        }

    segments = candidate_meta["segments"]
    start_run, end_run = materialize_anchor_span(paragraph, candidate, segments)
    if start_run is None or end_run is None:
        return None, {
            "strategy": "needs_human_review",
            "confidence": "low",
            "reason": "anchor span could not be materialized",
            "paragraph_number": paragraph_meta.get("paragraph_number"),
        }

    meta = {
        "strategy": candidate["anchor_source"],
        "confidence": candidate.get("confidence", "medium"),
        "paragraph_number": paragraph_meta.get("paragraph_number"),
        "paragraph_strategy": paragraph_meta.get("strategy"),
        "paragraph_confidence": paragraph_meta.get("confidence"),
        "match_method": candidate["match_method"],
        "anchor_source": candidate["anchor_source"],
        "span_start": candidate["start"],
        "span_end": candidate["end"],
        "matched_text": candidate.get("matched_text", ""),
        "ambiguous": candidate.get("ambiguous", False),
        "paragraph_ref": id(paragraph),
    }
    return (start_run, end_run), meta


def build_comment_text(issue: Dict[str, Any]) -> str:
    comment_text = (issue.get("comment_text") or "").strip()
    issue_type = issue.get("type", "问题")
    issue_desc = issue.get("issue", "需要复核")
    suggestion = issue.get("suggestion", "")
    original = issue.get("original") or issue.get("anchor_text") or ""

    normalized_comment = comment_text.replace("\r", "")
    if normalized_comment and (
        "\n" in normalized_comment
        or ("发现" in normalized_comment and "建议" in normalized_comment)
        or ("问题" in normalized_comment and "建议" in normalized_comment)
    ):
        return comment_text

    parts = []
    if issue_desc:
        parts.append(f"发现：{issue_desc}")
    elif issue_type:
        parts.append(f"发现：{issue_type}")

    if original:
        parts.append(f"原文：{original}")
    if suggestion:
        parts.append(f"建议：{suggestion}")
    if comment_text and comment_text not in issue_desc:
        parts.append(f"补充：{comment_text}")
    return "\n".join(parts)


def create_comment_definition(
    comments_root: etree._Element, comment_id: int, comment_text: str, author: str
) -> None:
    comment = etree.Element(f"{{{WML_NS}}}comment")
    comment.set(f"{W}id", str(comment_id))
    comment.set(f"{W}author", author)

    paragraph = etree.Element(f"{{{WML_NS}}}p")
    run = etree.Element(f"{{{WML_NS}}}r")

    lines = comment_text.splitlines() or [comment_text]
    for index, line in enumerate(lines):
        text = etree.Element(f"{{{WML_NS}}}t")
        text.set(XML_SPACE_ATTR, "preserve")
        text.text = line
        run.append(text)
        if index < len(lines) - 1:
            run.append(etree.Element(f"{{{WML_NS}}}br"))

    paragraph.append(run)
    comment.append(paragraph)
    comments_root.append(comment)


def insert_comment_range_over_runs(
    start_run: etree._Element,
    end_run: etree._Element,
    comment_id: int,
) -> None:
    parent = start_run.getparent()
    if parent is None or end_run.getparent() is not parent:
        raise ValueError("target runs must share the same parent paragraph")

    start = etree.Element(f"{{{WML_NS}}}commentRangeStart")
    start.set(f"{W}id", str(comment_id))

    end = etree.Element(f"{{{WML_NS}}}commentRangeEnd")
    end.set(f"{W}id", str(comment_id))

    reference_run = etree.Element(f"{{{WML_NS}}}r")
    reference = etree.Element(f"{{{WML_NS}}}commentReference")
    reference.set(f"{W}id", str(comment_id))
    reference_run.append(reference)

    children = list(parent)
    start_index = children.index(start_run)
    end_index = children.index(end_run)
    if start_index > end_index:
        raise ValueError("start run occurs after end run")

    parent.insert(start_index, start)
    parent.insert(end_index + 2, end)
    parent.insert(end_index + 3, reference_run)


def create_text_run(text: str) -> etree._Element:
    run = etree.Element(f"{{{WML_NS}}}r")
    text_node = etree.SubElement(run, f"{{{WML_NS}}}t")
    text_node.set(XML_SPACE_ATTR, "preserve")
    text_node.text = text
    return run


def append_paragraph_to_body(document_root: etree._Element, text: str) -> etree._Element:
    body = document_root.find("w:body", NS)
    if body is None:
        raise ValueError("document.xml missing w:body")

    paragraph = etree.Element(f"{{{WML_NS}}}p")
    paragraph.append(create_text_run(text))

    section_props = body.find("w:sectPr", NS)
    if section_props is not None:
        body.insert(list(body).index(section_props), paragraph)
    else:
        body.append(paragraph)
    return paragraph


def append_bottom_comment(
    document_root: etree._Element,
    issue: Dict[str, Any],
    comment_id: int,
    comments_root: etree._Element,
    author: str,
) -> Dict[str, Any]:
    issue_id = str(issue.get("id") or "").strip()
    severity = str(issue.get("severity") or "").strip()
    issue_text = str(issue.get("issue") or "需人工复核").strip()
    location = str(issue.get("location") or "").strip()
    prefix = f"{issue_id} " if issue_id else ""
    suffix = f"（原位置：{location}）" if location else ""
    visible_text = f"审核意见：{prefix}[{severity}] {issue_text}{suffix}".strip()
    paragraph = append_paragraph_to_body(document_root, visible_text)
    runs = direct_paragraph_runs(paragraph)
    if not runs:
        raise ValueError("appended paragraph has no text runs")
    insert_comment_range_over_runs(runs[0], runs[-1], comment_id)
    create_comment_definition(comments_root, comment_id, build_comment_text(issue), author)
    return {
        "strategy": "document_end",
        "confidence": "low",
        "match_method": "document_end",
        "paragraph_ref": id(paragraph),
        "span_start": 0,
        "span_end": len(visible_text),
    }


def add_comments_improved(
    input_docx: str,
    review_json: str,
    output_docx: str,
    author: str = "文件审核系统",
    *,
    strip_automated_comments: bool = True,
) -> Dict[str, Any]:
    with open(review_json, "r", encoding="utf-8-sig") as handle:
        review_data = json.load(handle)

    issues = review_data.get("issues", [])
    if not isinstance(issues, list):
        raise ValueError("review_json issues must be a list")

    warnings: List[Dict[str, Any]] = []
    positioning_stats = Counter()
    used_spans_by_paragraph: Dict[int, List[Tuple[int, int]]] = {}
    appended_heading = False

    try:
        with zipfile.ZipFile(input_docx, "r") as zip_ref:
            entries = {
                info.filename: zip_ref.read(info.filename)
                for info in zip_ref.infolist()
                if not info.is_dir()
            }

        if "word/document.xml" not in entries:
            raise ValueError("DOCX missing word/document.xml")

        document_tree = parse_xml_bytes(entries["word/document.xml"])
        document_root = document_tree.getroot()
        paragraphs = all_paragraphs(document_root)

        if "word/comments.xml" in entries:
            comments_tree = parse_xml_bytes(entries["word/comments.xml"])
        else:
            comments_tree = etree.ElementTree(etree.Element(f"{{{WML_NS}}}comments", nsmap={"w": WML_NS}))
        comments_root = comments_tree.getroot()

        if "word/_rels/document.xml.rels" in entries:
            rels_tree = parse_xml_bytes(entries["word/_rels/document.xml.rels"])
        else:
            rels_tree = etree.ElementTree(etree.Element(f"{{{REL_NS}}}Relationships", nsmap={None: REL_NS}))

        if "[Content_Types].xml" in entries:
            ct_tree = parse_xml_bytes(entries["[Content_Types].xml"])
        else:
            ct_tree = etree.ElementTree(etree.Element(f"{{{CT_NS}}}Types", nsmap={None: CT_NS}))

        ensure_comments_part(rels_tree, ct_tree, comments_root)

        removed_existing = (
            strip_prior_automated_comments(document_root, comments_root)
            if strip_automated_comments
            else 0
        )
        existing_total = len(existing_comment_ids(comments_root))
        comment_id = next_comment_id(comments_root)

        added = 0
        failed = 0
        expected = sum(
            1
            for issue in issues
            if issue.get("comments_added", 1) != 0
        )

        ordered_issues = sorted(issues, key=comment_order_key)
        for index, issue in enumerate(ordered_issues, start=1):
            try:
                if issue.get("comments_added") == 0:
                    continue

                target_range = None
                meta: Dict[str, Any] = {}
                planned_document_end = bool(issue.get("append_to_document_end"))
                if not planned_document_end:
                    target_range, meta = resolve_target_range(paragraphs, issue, used_spans_by_paragraph)
                if target_range is None:
                    if is_strict_agent_anchor_issue(issue) and not planned_document_end:
                        failed += 1
                        warnings.append(
                            {
                                "index": index,
                                "issue": issue.get("issue", "")[:80],
                                "location": issue.get("location", ""),
                                "reason": meta.get("reason") or "strict agent anchor could not be materialized",
                            }
                        )
                        continue
                    if not appended_heading:
                        append_paragraph_to_body(document_root, "未定位审核意见")
                        appended_heading = True
                    reason = meta.get("reason") or issue.get("appendix_reason") or "原位置未稳定定位，已追加到文末"
                    meta = append_bottom_comment(document_root, issue, comment_id, comments_root, author)
                    positioning_stats["by_document_end"] += 1
                    if planned_document_end:
                        positioning_stats["by_expected_document_end"] += 1
                    else:
                        positioning_stats["by_anchor_failure_document_end"] += 1
                    added += 1
                    comment_id += 1
                    warnings.append(
                        {
                            "index": index,
                            "issue": issue.get("issue", "")[:80],
                            "location": issue.get("location", ""),
                            "reason": reason,
                            "fallback": "document_end",
                        }
                    )
                    continue

                start_run, end_run = target_range
                if start_run is None or end_run is None:
                    failed += 1
                    warnings.append(
                        {
                            "index": index,
                            "issue": issue.get("issue", "")[:80],
                            "location": issue.get("location", ""),
                            "reason": "目标范围解析失败",
                        }
                    )
                    continue

                strategy = meta.get("match_method", "inference")
                if strategy == "span":
                    positioning_stats["by_span"] += 1
                elif strategy == "exact":
                    positioning_stats["by_exact_text"] += 1
                elif strategy == "contains":
                    positioning_stats["by_contains_text"] += 1
                else:
                    positioning_stats["by_inference"] += 1

                if meta.get("ambiguous"):
                    positioning_stats["by_ambiguous_anchor"] += 1
                    high_risk_ambiguous = is_high_risk_ambiguous_anchor(meta)
                    if high_risk_ambiguous:
                        positioning_stats["by_high_risk_ambiguous_anchor"] += 1
                    warnings.append(
                        {
                            "index": index,
                            "issue": issue.get("issue", "")[:80],
                            "location": issue.get("location", ""),
                            "strategy": meta.get("anchor_source", strategy),
                            "match_method": meta.get("match_method", ""),
                            "matched_text": meta.get("matched_text", ""),
                            "high_risk": high_risk_ambiguous,
                            "confidence": meta.get("confidence", "low"),
                            "reason": "多个候选片段命中，已选择首个稳定片段",
                        }
                    )

                insert_comment_range_over_runs(start_run, end_run, comment_id)
                parent = start_run.getparent()
                paragraph_ref = meta.get("paragraph_ref")
                span_start = meta.get("span_start")
                span_end = meta.get("span_end")
                if isinstance(paragraph_ref, int) and isinstance(span_start, int) and isinstance(span_end, int):
                    used_spans_by_paragraph.setdefault(paragraph_ref, []).append((span_start, span_end))
                create_comment_definition(
                    comments_root, comment_id, build_comment_text(issue), author
                )
                added += 1
                comment_id += 1
            except Exception as exc:
                failed += 1
                warnings.append(
                    {
                        "index": index,
                        "issue": issue.get("issue", "")[:80],
                        "location": issue.get("location", ""),
                        "reason": str(exc),
                    }
                )

        entries["word/document.xml"] = document_tree_to_bytes(document_tree)
        entries["word/comments.xml"] = tree_to_bytes(comments_tree)
        entries["word/_rels/document.xml.rels"] = tree_to_bytes(rels_tree)
        entries["[Content_Types].xml"] = tree_to_bytes(ct_tree)

        output_path = Path(output_docx)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_docx, "w", zipfile.ZIP_DEFLATED) as zip_out:
            for name, data in entries.items():
                zip_out.writestr(name, data)

        validation = validate_output_docx(output_docx)
        if not validation["valid"]:
            return {
                "success": False,
                "error": "生成后的 DOCX 通过基础写入但未通过有效性校验",
                "validation_report": validation,
            }

        return {
            "success": True,
            "message": f"成功添加 {added} 个批注",
            "comments_added": added,
            "comments_failed": failed,
            "comments_expected": expected,
            "comments_actual": existing_total + added,
            "existing_comments": existing_total,
            "removed_existing_comments": removed_existing,
            "positioning_quality": {
                "by_span": positioning_stats["by_span"],
                "by_exact_text": positioning_stats["by_exact_text"],
                "by_contains_text": positioning_stats["by_contains_text"],
                "by_inference": positioning_stats["by_inference"],
                "by_document_end": positioning_stats["by_document_end"],
                "by_expected_document_end": positioning_stats["by_expected_document_end"],
                "by_anchor_failure_document_end": positioning_stats["by_anchor_failure_document_end"],
                "by_ambiguous_anchor": positioning_stats["by_ambiguous_anchor"],
                "by_high_risk_ambiguous_anchor": positioning_stats["by_high_risk_ambiguous_anchor"],
                "failed": failed,
                "warnings": warnings,
            },
            "validation_report": validation,
            "output_document": output_docx,
        }
    except Exception as exc:
        import traceback

        return {
            "success": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def main() -> int:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = parse_args()

    if not os.path.exists(args.input_docx):
        print(f"错误: 输入文档不存在: {args.input_docx}")
        return 1
    if not os.path.exists(args.review_json):
        print(f"错误: 审核结果文件不存在: {args.review_json}")
        return 1

    print("=" * 70)
    print("Word Comment Writer")
    print("=" * 70)
    print(f"输入文档: {args.input_docx}")
    print(f"审核结果: {args.review_json}")
    print(f"输出文档: {args.output_docx}")
    print()

    result = add_comments_improved(
        args.input_docx,
        args.review_json,
        args.output_docx,
        author=args.author,
        strip_automated_comments=not args.preserve_automated_comments,
    )

    print("=" * 70)
    if result.get("success"):
        print("批注添加成功")
        print(f"   预计批注数: {result['comments_expected']}")
        print(f"   新增批注数: {result['comments_added']}")
        print(f"   失败批注数: {result['comments_failed']}")
        print(f"   旧批注数: {result['existing_comments']}")
        print(f"   已剥离旧自动批注数: {result.get('removed_existing_comments', 0)}")
        print(f"   输出文档: {result['output_document']}")
        validation = result.get("validation_report")
        if validation:
            print()
            print("文档有效性校验:")
            print(f"   通过: {validation.get('valid')}")
            print(f"   批注标记: start={validation.get('document_comment_starts')}, end={validation.get('document_comment_ends')}, ref={validation.get('document_comment_refs')}")
            print(f"   批注定义: {validation.get('comment_definitions')}")
            if validation.get("errors"):
                print(f"   错误: {validation.get('errors')}")
            if validation.get("warnings"):
                print(f"   警告: {validation.get('warnings')}")
        stats = result["positioning_quality"]
        print()
        print("定位统计:")
        print(f"   精确片段定位: {stats['by_span']}")
        print(f"   精确文本定位: {stats['by_exact_text']}")
        print(f"   模糊文本定位: {stats['by_contains_text']}")
        print(f"   兜底/推断定位: {stats['by_inference']}")
        print(f"   定位失败: {stats['failed']}")
        if stats["warnings"]:
            print()
            print(f"警告: {len(stats['warnings'])} 条")
            for warning in stats["warnings"][:10]:
                print(f" - {warning}")
    else:
        print("批注添加失败")
        print(f"   错误: {result.get('error', 'Unknown error')}")
        if result.get("traceback"):
            print()
            print(result["traceback"])
        return 1

    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

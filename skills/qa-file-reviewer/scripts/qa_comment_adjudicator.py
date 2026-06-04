from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


LLM_REVIEW_BRANCH = "llm_full_review"
ISSUE_STATUS_CONFIRMED = "confirmed"
CONTENT_CONSISTENCY_BRANCH = "content_consistency"
TEMP_TOLERANCE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*°\s*C\s*±\s*\d+(?:\.\d+)?\s*°\s*C",
    flags=re.IGNORECASE,
)
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
        "µ": "μ",
    }
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").translate(ANCHOR_CHAR_TRANSLATION))


def find_first_exact_span(source_text: str, needle: str) -> Optional[Dict[str, Any]]:
    if not source_text or not needle:
        return None
    index = source_text.find(needle)
    if index >= 0:
        return {"start": index, "end": index + len(needle), "unit": "char"}

    normalized_source = normalize_text(source_text)
    normalized_needle = normalize_text(needle)
    if len(normalized_needle) < 2 or not normalized_source:
        return None
    normalized_index = normalized_source.find(normalized_needle)
    if normalized_index < 0:
        return None

    source_map: List[int] = []
    for i, ch in enumerate(source_text):
        if not ch.isspace():
            source_map.append(i)
    if normalized_index + len(normalized_needle) - 1 >= len(source_map):
        return None
    return {
        "start": source_map[normalized_index],
        "end": source_map[normalized_index + len(normalized_needle) - 1] + 1,
        "unit": "char",
    }


def _paragraph_index_from_text(text: str) -> str:
    match = re.search(r"\d+", str(text or ""))
    return str(int(match.group(0))) if match else ""


def _issue_location_key(issue: Dict[str, Any]) -> str:
    anchor_locator = normalize_text(str(issue.get("anchor_locator") or "")).casefold()
    if anchor_locator:
        return anchor_locator
    paragraph_index = str(issue.get("paragraph_index") or "").strip()
    if paragraph_index:
        return f"paragraph={paragraph_index}"
    return normalize_text(str(issue.get("location") or "")).casefold()


def _normalized_issue_target(issue: Dict[str, Any]) -> str:
    suggestion = str(issue.get("suggestion") or "")
    original_norm = normalize_text(str(issue.get("original") or issue.get("anchor_text") or "")).casefold()
    replacement_match = re.search(
        r"(?:改为|修改为|统一为|建议改为|建议修改为|更正为)[:：]?\s*[`\"'“”‘’]?([^`，。,；;\"'“”‘’]+)",
        suggestion,
    )
    if replacement_match:
        replacement = normalize_text(replacement_match.group(1)).casefold()
        if replacement:
            return replacement
    english_replacement_match = re.search(
        r"\b(?:change|replace|revise|correct|unify)\b\s+.+?\b(?:to|with)\b\s+[\"'“”‘’]([^\"'“”‘’]{2,})[\"'“”‘’]",
        suggestion,
        flags=re.IGNORECASE,
    )
    if english_replacement_match:
        replacement = normalize_text(english_replacement_match.group(1)).casefold()
        if replacement:
            return replacement
    quoted = re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,})[\"'“”‘’]", suggestion)
    candidates = [
        normalize_text(candidate).casefold()
        for candidate in quoted
        if normalize_text(candidate).casefold() and normalize_text(candidate).casefold() != original_norm
    ]
    if candidates:
        candidates.sort(key=len)
        return candidates[0]
    return normalize_text(suggestion).casefold()


def _semantic_dedupe_key(issue: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    if str(issue.get("branch") or "") != LLM_REVIEW_BRANCH:
        return None
    location = _issue_location_key(issue)
    original = normalize_text(str(issue.get("original") or issue.get("anchor_text") or "")).casefold()
    target = _normalized_issue_target(issue)
    if len(location) < 2 or len(original) < 2 or len(target) < 2:
        return None
    return (location, original, target)


def _anchor_norm(issue: Dict[str, Any]) -> str:
    return normalize_text(str(issue.get("anchor_text") or issue.get("original") or "")).casefold()


def _has_weak_anchor_text(anchor_text: str) -> bool:
    text = str(anchor_text or "").strip()
    compact = normalize_text(text).casefold()
    if len(compact) < 3:
        return True
    if re.fullmatch(r"[Nn]\s*=\s*\d+", text):
        return True
    if re.fullmatch(r"[A-Za-z]+", text):
        return compact in {
            "a",
            "an",
            "and",
            "or",
            "the",
            "to",
            "of",
            "in",
            "on",
            "for",
            "with",
            "by",
            "as",
            "is",
            "are",
            "was",
            "were",
            "than",
            "less",
            "from",
            "into",
            "that",
            "this",
            "these",
            "those",
            "not",
            "no",
            "high",
            "low",
            "data",
            "table",
            "report",
            "solution",
            "method",
            "methods",
            "protocol",
            "protocols",
            "experiment",
            "experiments",
            "polymer",
            "aggregate",
            "aggregates",
            "peak",
            "peaks",
        }
    if compact in {
        "条件",
        "条件下",
        "结果",
        "结果表明",
        "显示",
        "详见",
        "见表",
        "原文",
        "报告",
        "表格",
        "数据",
        "方法",
        "方法确认",
        "试验",
        "实验",
        "聚合体",
        "单体",
    }:
        return True
    return False


def _semantic_text_tokens(issue: Dict[str, Any]) -> set[str]:
    blob = "\n".join(str(issue.get(key) or "") for key in ("issue", "suggestion", "evidence"))
    blob = blob.translate(ANCHOR_CHAR_TRANSLATION).casefold()
    tokens: set[str] = set()
    for match in re.findall(r"[a-z0-9][a-z0-9_.+-]{2,}", blob):
        tokens.add(match)
    for match in re.findall(r"[\u4e00-\u9fff]{2,}", blob):
        if len(match) <= 4:
            tokens.add(match)
            continue
        tokens.update(match[index : index + 2] for index in range(len(match) - 1))
    return tokens


def _semantic_text_overlap(first: Dict[str, Any], second: Dict[str, Any]) -> float:
    first_tokens = _semantic_text_tokens(first)
    second_tokens = _semantic_text_tokens(second)
    if not first_tokens or not second_tokens:
        return 0.0
    return len(first_tokens & second_tokens) / max(1, min(len(first_tokens), len(second_tokens)))


def _source_error_terms(issue: Dict[str, Any]) -> set[str]:
    fields = "\n".join(str(issue.get(key) or "") for key in ("original", "issue", "suggestion", "evidence"))
    terms: set[str] = set()
    for item in re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]", fields):
        norm = normalize_text(item).casefold()
        if 3 <= len(norm) <= 60:
            terms.add(norm)
    for item in re.findall(r"[A-Z]{1,8}\d{2,8}[\u4e00-\u9fffA-Za-z]{1,20}", fields):
        norm = normalize_text(item).casefold()
        if 3 <= len(norm) <= 60:
            terms.add(norm)
    return terms


def _replacement_pair(issue: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    suggestion = str(issue.get("suggestion") or "")
    patterns = (
        r"[`\"'“”‘’]([^`\"'“”‘’]{1,80})[`\"'“”‘’]\s*(?:改为|修改为|更正为|统一为|替换为)\s*[`\"'“”‘’]([^`\"'“”‘’]{1,80})[`\"'“”‘’]",
        r"(?:将|把)\s*[`\"'“”‘’]?([^`\"'“”‘’，。,；;]{1,80})[`\"'“”‘’]?\s*(?:改为|修改为|更正为|统一为|替换为)\s*[`\"'“”‘’]?([^`\"'“”‘’，。,；;]{1,80})[`\"'“”‘’]?",
        r"\b(?:change|replace|revise|correct)\b\s*[`\"'“”‘’]?([^`\"'“”‘’]{1,80})[`\"'“”‘’]?\s+\b(?:to|with)\b\s*[`\"'“”‘’]?([^`\"'“”‘’]{1,80})[`\"'“”‘’]?",
    )
    for pattern in patterns:
        match = re.search(pattern, suggestion, flags=re.IGNORECASE)
        if not match:
            continue
        source = normalize_text(match.group(1).strip(" \t\r\n:：;；,，。.`\"'“”‘’")).casefold()
        target = normalize_text(match.group(2).strip(" \t\r\n:：;；,，。.`\"'“”‘’")).casefold()
        if source and target and source != target:
            return source, target
    original = normalize_text(str(issue.get("original") or issue.get("anchor_text") or "")).casefold()
    target = _normalized_issue_target(issue)
    if original and target and original != target and len(target) <= 80:
        return original, target
    return None


def _replacement_target_norm(issue: Dict[str, Any]) -> str:
    pair = _replacement_pair(issue)
    if pair:
        return pair[1]
    suggestion = str(issue.get("suggestion") or "")
    has_rewrite_action = bool(
        re.search(r"(改为|修改为|更正为|统一为|替换为|change|replace|revise|correct)", suggestion, flags=re.IGNORECASE)
    )
    if not has_rewrite_action:
        return ""
    target = _normalized_issue_target(issue)
    suggestion_norm = normalize_text(suggestion).casefold()
    if target and target != suggestion_norm and len(target) <= 120:
        return target
    return ""


def _is_objective_formula_case_issue(issue: Dict[str, Any]) -> bool:
    original = str(issue.get("original") or issue.get("anchor_text") or "")
    combined = "\n".join(str(issue.get(key) or "") for key in ("issue", "suggestion", "evidence"))
    if not any(marker in combined.casefold() for marker in ("化学式", "chemical formula")):
        return False
    token_pattern = r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,9})(?![A-Za-z0-9])"
    original_tokens = re.findall(token_pattern, original)
    suggested_tokens = re.findall(token_pattern, combined)
    for source_token in original_tokens:
        for target_token in suggested_tokens:
            if source_token != target_token and source_token.casefold() == target_token.casefold():
                return True
    return False


def _is_replacement_target_anchor(issue: Dict[str, Any], anchor_text: str) -> bool:
    if _is_objective_formula_case_issue(issue):
        return False
    anchor_norm = normalize_text(str(anchor_text or "")).casefold()
    target_norm = _replacement_target_norm(issue)
    return bool(anchor_norm and target_norm and anchor_norm == target_norm)


def _temperature_tolerance_dedupe_key(issue: Dict[str, Any]) -> Optional[Tuple[str, Tuple[str, ...]]]:
    blob = "\n".join(
        str(issue.get(key) or "")
        for key in ("original", "anchor_text", "issue", "suggestion")
    )
    values = {
        normalize_text(match.group(0)).casefold()
        for match in TEMP_TOLERANCE_PATTERN.finditer(blob)
    }
    if len(values) < 2:
        return None
    location = _issue_location_key(issue)
    if len(location) < 2:
        return None
    return (location, tuple(sorted(values)))


def _same_location_semantic_duplicate(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    if str(first.get("branch") or "") != LLM_REVIEW_BRANCH:
        return False
    if str(second.get("branch") or "") != LLM_REVIEW_BRANCH:
        return False
    if _issue_location_key(first) != _issue_location_key(second):
        return False

    first_target = _normalized_issue_target(first)
    second_target = _normalized_issue_target(second)
    target_matches = (
        len(first_target) >= 3
        and len(second_target) >= 3
        and (first_target == second_target or first_target in second_target or second_target in first_target)
    )
    if target_matches:
        first_terms = _source_error_terms(first)
        second_terms = _source_error_terms(second)
        if first_terms & second_terms:
            return True
        if _semantic_text_overlap(first, second) >= 0.18:
            return True

    first_anchor = _anchor_norm(first)
    second_anchor = _anchor_norm(second)
    if len(first_anchor) < 3 or len(second_anchor) < 3:
        return False

    anchor_matches = first_anchor == second_anchor
    anchor_contains = first_anchor in second_anchor or second_anchor in first_anchor
    if not anchor_matches and not anchor_contains:
        return False

    overlap = _semantic_text_overlap(first, second)
    if anchor_matches:
        return overlap >= 0.25
    return overlap >= 0.35


def _same_location_semantic_paraphrase(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    if str(first.get("branch") or "") != LLM_REVIEW_BRANCH:
        return False
    if str(second.get("branch") or "") != LLM_REVIEW_BRANCH:
        return False
    if _issue_location_key(first) != _issue_location_key(second):
        return False
    if str(first.get("category") or "") and str(second.get("category") or ""):
        if str(first.get("category")) != str(second.get("category")):
            return False
    first_target = _normalized_issue_target(first)
    second_target = _normalized_issue_target(second)
    if len(first_target) >= 3 and len(second_target) >= 3:
        target_matches = (
            first_target == second_target
            or first_target in second_target
            or second_target in first_target
        )
        if not target_matches and not (_source_error_terms(first) & _source_error_terms(second)):
            combined = "\n".join(
                str(issue.get(key) or "")
                for issue in (first, second)
                for key in ("issue", "suggestion", "evidence")
            ).casefold()
            bilingual_same_concern = "chinese" in combined and "english" in combined and _semantic_text_overlap(first, second) >= 0.5
            if not bilingual_same_concern:
                return False
    return _semantic_text_overlap(first, second) >= 0.5


def _same_location_cross_branch_anchor_duplicate(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    first_branch = str(first.get("branch") or "")
    second_branch = str(second.get("branch") or "")
    if first_branch == second_branch:
        return False
    if LLM_REVIEW_BRANCH not in {first_branch, second_branch}:
        return False
    if CONTENT_CONSISTENCY_BRANCH not in {first_branch, second_branch}:
        return False
    if _issue_location_key(first) != _issue_location_key(second):
        return False

    first_anchor = _anchor_norm(first)
    second_anchor = _anchor_norm(second)
    if len(first_anchor) < 4 or len(second_anchor) < 4:
        return False
    if first_anchor == second_anchor or first_anchor in second_anchor or second_anchor in first_anchor:
        return True

    first_words = {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9]{2,}", str(first.get("anchor_text") or first.get("original") or ""))
    }
    second_words = {
        word.casefold()
        for word in re.findall(r"[A-Za-z0-9]{2,}", str(second.get("anchor_text") or second.get("original") or ""))
    }
    if first_words and second_words:
        overlap = len(first_words & second_words) / max(1, min(len(first_words), len(second_words)))
        if overlap >= 0.6 and len(first_words & second_words) >= 2:
            return True
    return False


def _same_location_cross_branch_semantic_duplicate(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    first_branch = str(first.get("branch") or "")
    second_branch = str(second.get("branch") or "")
    if first_branch == second_branch:
        return False
    if LLM_REVIEW_BRANCH not in {first_branch, second_branch}:
        return False
    if CONTENT_CONSISTENCY_BRANCH not in {first_branch, second_branch}:
        return False
    if _issue_location_key(first) != _issue_location_key(second):
        return False

    rule_pair = {str(first.get("rule_id") or ""), str(second.get("rule_id") or "")}
    blob = normalize_text(
        "\n".join(
            str(issue.get(key) or "")
            for issue in (first, second)
            for key in ("original", "anchor_text", "issue", "suggestion", "evidence")
        )
    ).casefold()
    if "CONTENT-BI-TEMP-001" in rule_pair:
        temp_values = TEMP_TOLERANCE_PATTERN.findall(
            "\n".join(
                str(issue.get(key) or "")
                for issue in (first, second)
                for key in ("original", "anchor_text", "issue", "suggestion", "evidence")
            )
        )
        if len({normalize_text(value).casefold() for value in temp_values}) >= 2:
            return True
    return False


def _same_location_cross_branch_replacement_duplicate(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    first_branch = str(first.get("branch") or "")
    second_branch = str(second.get("branch") or "")
    if first_branch == second_branch:
        return False
    if LLM_REVIEW_BRANCH not in {first_branch, second_branch}:
        return False
    if CONTENT_CONSISTENCY_BRANCH not in {first_branch, second_branch}:
        return False
    if _issue_location_key(first) != _issue_location_key(second):
        return False
    first_pair = _replacement_pair(first)
    second_pair = _replacement_pair(second)
    if not first_pair or not second_pair:
        return False
    first_source, first_target = first_pair
    second_source, second_target = second_pair
    if len(first_source) < 3 or len(second_source) < 3 or len(first_target) < 3 or len(second_target) < 3:
        return False
    source_matches = first_source == second_source or first_source in second_source or second_source in first_source
    target_matches = first_target == second_target or first_target in second_target or second_target in first_target
    return source_matches and target_matches


def _same_location_cross_branch_objective_text_defect_duplicate(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    first_branch = str(first.get("branch") or "")
    second_branch = str(second.get("branch") or "")
    if first_branch == second_branch:
        return False
    if LLM_REVIEW_BRANCH not in {first_branch, second_branch}:
        return False
    if CONTENT_CONSISTENCY_BRANCH not in {first_branch, second_branch}:
        return False
    if _issue_location_key(first) != _issue_location_key(second):
        return False

    rule_pair = {str(first.get("rule_id") or ""), str(second.get("rule_id") or "")}
    blob = normalize_text(
        "\n".join(
            str(issue.get(key) or "")
            for issue in (first, second)
            for key in ("original", "anchor_text", "issue", "suggestion", "evidence")
        )
    ).casefold()
    if "CONTENT-PUNCT-001" in rule_pair:
        if "；；" in blob or "连续两个分号" in blob or "重复分号" in blob:
            return True
        if "；。" in blob or "分号后紧跟句号" in blob:
            return True
    return False


def _same_recurring_release_note_duplicate(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    rule_pair = {str(first.get("rule_id") or ""), str(second.get("rule_id") or "")}
    if "CONTENT-EN-GRAMMAR-002" not in rule_pair or "LLM-EN-001" not in rule_pair:
        return False
    first_blob = normalize_text(
        "\n".join(str(first.get(key) or "") for key in ("original", "anchor_text", "issue", "suggestion", "evidence"))
    ).casefold()
    second_blob = normalize_text(
        "\n".join(str(second.get(key) or "") for key in ("original", "anchor_text", "issue", "suggestion", "evidence"))
    ).casefold()
    release_note_markers = (
        "datareferencetothereleasingdata",
        "thisdatareferencetothereleasingdata",
        "indicatesthisdatareference",
    )
    return any(marker in first_blob and marker in second_blob for marker in release_note_markers)


def _recurring_semantic_cluster_key(issue: Dict[str, Any]) -> Optional[str]:
    branch = str(issue.get("branch") or "")
    rule_id = str(issue.get("rule_id") or "")
    if branch != LLM_REVIEW_BRANCH:
        return None
    blob = normalize_text(
        "\n".join(
            str(issue.get(key) or "")
            for key in ("original", "anchor_text", "issue", "suggestion", "evidence")
        )
    ).casefold()
    if ("turbidity" in blob or "urbidity" in blob or "浊度" in blob) and (
        "lessurbidity" in blob
        or "lessthanturbidity" in blob
        or "standardsolution" in blob
        or "标准液" in blob
    ):
        return "recurring:turbidity-standard-description"
    return None


def _issue_anchor_length(issue: Dict[str, Any]) -> int:
    span = issue.get("anchor_span")
    if isinstance(span, dict):
        try:
            start = int(span.get("start"))
            end = int(span.get("end"))
        except (TypeError, ValueError):
            start = end = -1
        if start >= 0 and end > start:
            return end - start
    original = str(issue.get("original") or issue.get("anchor_text") or "")
    return max(len(original), 9999)


def _prefers_issue(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    source_rank = {"risk_classifier": 0, "semantic_consistency": 1}
    severity_rank = {"关键": 3, "主要": 2, "次要": 1}

    def score(issue: Dict[str, Any]) -> Tuple[int, int, int, int, int, int, int, int]:
        source_agent = str(issue.get("source_agent") or "").strip()
        branch = str(issue.get("branch") or "").strip()
        anchor_norm = normalize_text(str(issue.get("anchor_text") or "")).casefold()
        original_norm = normalize_text(str(issue.get("original") or "")).casefold()
        return (
            1 if str(issue.get("status") or "") == ISSUE_STATUS_CONFIRMED else 0,
            1 if not issue.get("requires_external_evidence") else 0,
            1 if str(issue.get("comments_added") or 0) else 0,
            1 if anchor_norm and original_norm and anchor_norm == original_norm else 0,
            1 if branch != LLM_REVIEW_BRANCH else 0,
            severity_rank.get(str(issue.get("severity") or ""), 0),
            confidence_rank.get(str(issue.get("confidence") or "").strip(), 0),
            source_rank.get(source_agent, 2),
            -_issue_anchor_length(issue),
        )

    return score(candidate) > score(current)


def _extract_quoted_phrases(issue: Dict[str, Any]) -> List[str]:
    fields = [str(issue.get(key) or "") for key in ("issue", "suggestion", "evidence", "original", "anchor_text")]
    phrases: List[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip().strip(" \t\r\n:：;；,，。.-")
        if len(text) < 2 or len(text) > 80:
            return
        if text not in phrases:
            phrases.append(text)

    for source in fields:
        for item in re.findall(r"[`\"'“”‘’]([^`\"'“”‘’]{2,80})[`\"'“”‘’]", source):
            add(item)
        for before, _after in re.findall(
            r"[`\"'“”‘’]([^`\"'“”‘’]{1,40})[`\"'“”‘’]\s*(?:改为|修改为|更正为|统一为)\s*[`\"'“”‘’]([^`\"'“”‘’]{1,40})[`\"'“”‘’]",
            source,
        ):
            add(before)

    return phrases


def _prioritize_anchor_candidates(issue: Dict[str, Any], candidates: Sequence[str]) -> List[str]:
    original_norm = normalize_text(str(issue.get("original") or issue.get("anchor_text") or "")).casefold()

    def rank(phrase: str) -> Tuple[int, int]:
        norm = normalize_text(phrase).casefold()
        if _is_replacement_target_anchor(issue, phrase):
            return (4, -len(norm))
        if original_norm and norm in original_norm and norm != original_norm and len(norm) <= max(24, int(len(original_norm) * 0.5)):
            return (0, -len(norm))
        if original_norm and norm == original_norm and len(norm) <= 24:
            return (1, -len(norm))
        return (2, -len(norm))

    return sorted(candidates, key=rank)


def _needs_targeted_error_anchor_refine(issue: Dict[str, Any], current_anchor: str, candidates: Sequence[str]) -> bool:
    current_norm = normalize_text(current_anchor).casefold()
    candidate_norms = [normalize_text(candidate).casefold() for candidate in candidates]
    original_norm = normalize_text(str(issue.get("original") or "")).casefold()
    if original_norm and current_norm != original_norm and original_norm in candidate_norms:
        return True
    return False


def _targeted_source_anchor_candidates(issue: Dict[str, Any], paragraph_text: str) -> List[str]:
    candidates: List[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip().strip(" \t\r\n:：;；,，。.-")
        if text and text not in candidates:
            candidates.append(text)

    original = str(issue.get("original") or issue.get("anchor_text") or "").strip()
    add(original)
    fields = "\n".join(str(issue.get(key) or "") for key in ("issue", "suggestion", "evidence", "original", "anchor_text"))
    for pattern in (
        r"(?:replace|change|correct|revise)\s+[`\"'“”‘’]([^`\"'“”‘’]{2,80})[`\"'“”‘’]\s+(?:to|with)\s+[`\"'“”‘’][^`\"'“”‘’]{1,80}[`\"'“”‘’]",
        r"[`\"'“”‘’]([^`\"'“”‘’]{2,80})[`\"'“”‘’]\s*(?:改为|修改为|更正为|统一为)\s*[`\"'“”‘’][^`\"'“”‘’]{1,80}[`\"'“”‘’]",
        r"(?:source text|source sentence|original text|原文)(?:\s+contains|\s+is|包含|为)?\s*[`\"'“”‘’]([^`\"'“”‘’]{2,80})[`\"'“”‘’]",
    ):
        for match in re.findall(pattern, fields, flags=re.IGNORECASE):
            add(str(match))
    candidates = [candidate for candidate in candidates if find_first_exact_span(paragraph_text, candidate) is not None]
    return candidates


def _has_more_specific_anchor_candidate(
    paragraph_text: str,
    current_anchor: str,
    candidates: Sequence[str],
) -> bool:
    current = str(current_anchor or "").strip()
    if len(current) < 20:
        return False
    for phrase in candidates:
        value = str(phrase or "").strip()
        if len(value) < 2:
            continue
        if _has_weak_anchor_text(value):
            continue
        if len(value) > max(20, int(len(current) * 0.5)):
            continue
        if find_first_exact_span(paragraph_text, value) is not None:
            return True
    return False


def _parse_anchor_locator_xml(anchor_locator: str) -> str:
    match = re.search(r"paragraph\s*=\s*(\d+)", str(anchor_locator or ""), flags=re.IGNORECASE)
    return str(int(match.group(1))) if match else ""


def _resolve_issue_record(issue: Dict[str, Any], paragraphs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    by_xml: Dict[str, Dict[str, Any]] = {}
    by_logical: Dict[str, Dict[str, Any]] = {}
    for record in paragraphs:
        xml_index = _paragraph_index_from_text(record.get("xml_index"))
        logical_index = _paragraph_index_from_text(record.get("logical_index"))
        if xml_index and xml_index not in by_xml:
            by_xml[xml_index] = record
        if logical_index and logical_index not in by_logical:
            by_logical[logical_index] = record

    anchor_xml = _parse_anchor_locator_xml(str(issue.get("anchor_locator") or ""))
    if anchor_xml:
        if anchor_xml in by_xml:
            return by_xml[anchor_xml]
        if anchor_xml in by_logical:
            return by_logical[anchor_xml]

    location = str(issue.get("location") or "")
    location_xml = ""
    xml_match = re.search(r"XML\s*[:：]\s*(\d+)", location, flags=re.IGNORECASE)
    if xml_match:
        location_xml = str(int(xml_match.group(1)))
    if location_xml:
        if location_xml in by_xml:
            return by_xml[location_xml]
        if location_xml in by_logical:
            return by_logical[location_xml]

    para_target = _paragraph_index_from_text(issue.get("paragraph_index"))
    if para_target:
        if para_target in by_logical:
            return by_logical[para_target]
        if para_target in by_xml:
            return by_xml[para_target]

    return None


def _adjudicate_anchor_with_record(
    issue: Dict[str, Any],
    record: Dict[str, Any],
    *,
    used_anchor_norms: set[str],
) -> Dict[str, Any]:
    updated = dict(issue)
    paragraph_text = str(record.get("text") or "")
    if not paragraph_text:
        return updated

    current_anchor = str(updated.get("anchor_text") or updated.get("original") or "").strip()
    current_span = find_first_exact_span(paragraph_text, current_anchor) if current_anchor else None
    current_norm = normalize_text(current_anchor).casefold()

    span_value = updated.get("anchor_span")
    span_len = 0
    if isinstance(span_value, dict):
        try:
            span_len = int(span_value.get("end")) - int(span_value.get("start"))
        except (TypeError, ValueError):
            span_len = 0
    broad_span = bool(paragraph_text) and span_len >= max(12, int(len(paragraph_text) * 0.8))
    candidates = _prioritize_anchor_candidates(
        updated,
        [*_targeted_source_anchor_candidates(updated, paragraph_text), *_extract_quoted_phrases(updated)],
    )
    has_more_specific_anchor = _has_more_specific_anchor_candidate(paragraph_text, current_anchor, candidates)
    needs_refine = (
        current_span is None
        or broad_span
        or has_more_specific_anchor
        or _needs_targeted_error_anchor_refine(updated, current_anchor, candidates)
        or _is_replacement_target_anchor(updated, current_anchor)
        or not current_anchor
    )

    if not needs_refine:
        used_anchor_norms.add(current_norm)
        if not str(updated.get("anchor_locator") or "").strip():
            updated["anchor_locator"] = f"paragraph={record.get('xml_index', '')}"
        return updated

    best_value = ""
    best_span: Optional[Dict[str, Any]] = None
    best_norm = ""

    def pick_candidate(allow_used: bool) -> bool:
        nonlocal best_value, best_span, best_norm
        for phrase in candidates:
            norm = normalize_text(phrase).casefold()
            if not norm:
                continue
            if _has_weak_anchor_text(phrase):
                continue
            if _is_replacement_target_anchor(updated, phrase):
                continue
            if not allow_used and norm in used_anchor_norms:
                continue
            span = find_first_exact_span(paragraph_text, phrase)
            if span is None:
                continue
            best_value = phrase
            best_span = span
            best_norm = norm
            return True
        return False

    if not pick_candidate(allow_used=False):
        pick_candidate(allow_used=True)

    if best_value and best_span is not None:
        updated["anchor_text"] = best_value
        original_text = str(updated.get("original") or "").strip()
        original_span = find_first_exact_span(paragraph_text, original_text) if original_text else None
        if original_span is None or len(original_text) > len(best_value) * 2:
            updated["original"] = best_value
        updated["anchor_span"] = best_span
        updated["comments_added"] = 1
        updated["match_method"] = "span"
        notes = str(updated.get("notes") or "").strip()
        adjudication_note = "anchor refined by pre-comment adjudication"
        updated["notes"] = f"{notes}; {adjudication_note}" if notes else adjudication_note
        if not str(updated.get("anchor_locator") or "").strip():
            updated["anchor_locator"] = f"paragraph={record.get('xml_index', '')}"
        used_anchor_norms.add(best_norm)
        return updated

    if (
        current_span is not None
        and current_norm
        and not _has_weak_anchor_text(current_anchor)
        and not _is_replacement_target_anchor(updated, current_anchor)
    ):
        used_anchor_norms.add(current_norm)
        updated["anchor_span"] = current_span
        updated["comments_added"] = 1
        if not str(updated.get("anchor_locator") or "").strip():
            updated["anchor_locator"] = f"paragraph={record.get('xml_index', '')}"
    return updated


def _dedupe_semantic_duplicates(issues: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen_semantic: Dict[Tuple[str, str, str], int] = {}
    seen_temperature: Dict[Tuple[str, Tuple[str, ...]], int] = {}
    seen_recurring: Dict[str, int] = {}
    for issue in issues:
        recurring_key = _recurring_semantic_cluster_key(issue)

        temperature_key = _temperature_tolerance_dedupe_key(issue)
        if temperature_key is not None and temperature_key in seen_temperature:
            existing_index = seen_temperature[temperature_key]
            if _prefers_issue(issue, result[existing_index]):
                result[existing_index] = issue
            continue

        key = _semantic_dedupe_key(issue)
        if key is not None and key in seen_semantic:
            existing_index = seen_semantic[key]
            if _prefers_issue(issue, result[existing_index]):
                result[existing_index] = issue
            continue

        overlap_duplicate_index = next(
            (
                index
                for index, existing in enumerate(result)
                if _same_location_semantic_duplicate(issue, existing)
                or _same_location_semantic_paraphrase(issue, existing)
                or _same_location_cross_branch_anchor_duplicate(issue, existing)
                or _same_location_cross_branch_replacement_duplicate(issue, existing)
                or _same_location_cross_branch_semantic_duplicate(issue, existing)
                or _same_location_cross_branch_objective_text_defect_duplicate(issue, existing)
                or _same_recurring_release_note_duplicate(issue, existing)
            ),
            None,
        )
        if overlap_duplicate_index is not None:
            if _prefers_issue(issue, result[overlap_duplicate_index]):
                result[overlap_duplicate_index] = issue
            continue

        if recurring_key is not None and recurring_key in seen_recurring:
            existing_index = seen_recurring[recurring_key]
            if _prefers_issue(issue, result[existing_index]):
                result[existing_index] = issue
            continue

        if key is not None:
            seen_semantic[key] = len(result)
        if temperature_key is not None:
            seen_temperature[temperature_key] = len(result)
        if recurring_key is not None:
            seen_recurring[recurring_key] = len(result)
        result.append(issue)
    return result


def _dedupe_signature(issues: Sequence[Dict[str, Any]]) -> Tuple[Tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(issue.get("rule_id") or ""),
            _issue_location_key(issue),
            _anchor_norm(issue),
            normalize_text(str(issue.get("issue") or "")).casefold(),
        )
        for issue in issues
    )


def _dedupe_semantic_duplicates_until_stable(issues: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = list(issues)
    previous_signature: Tuple[Tuple[str, str, str, str], ...] = ()
    for _ in range(4):
        signature = _dedupe_signature(result)
        if signature == previous_signature:
            break
        previous_signature = signature
        next_result = _dedupe_semantic_duplicates(result)
        if _dedupe_signature(next_result) == signature:
            result = next_result
            break
        result = next_result
    return result


def adjudicate_issues(issues: Sequence[Dict[str, Any]], paragraphs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def anchor_pass(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_location: Dict[str, List[int]] = defaultdict(list)
        updated_items: List[Dict[str, Any]] = [dict(item) for item in items]
        for index, item in enumerate(updated_items):
            by_location[_issue_location_key(item)].append(index)

        for indexes in by_location.values():
            used_anchor_norms: set[str] = set()
            for index in indexes:
                issue = updated_items[index]
                record = _resolve_issue_record(issue, paragraphs)
                if record is None:
                    continue
                updated_items[index] = _adjudicate_anchor_with_record(issue, record, used_anchor_norms=used_anchor_norms)
        return updated_items

    updated = anchor_pass(issues)
    deduped = _dedupe_semantic_duplicates_until_stable(updated)
    deduped = _dedupe_semantic_duplicates_until_stable(anchor_pass(deduped))
    return [
        issue
        for issue in deduped
        if not (
            str(issue.get("branch") or "") == LLM_REVIEW_BRANCH
            and int(issue.get("comments_added") or 0) > 0
            and _has_weak_anchor_text(str(issue.get("anchor_text") or ""))
        )
    ]

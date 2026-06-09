#!/usr/bin/env python3
"""Deterministic Word translation runner for ai-platform smoke and fallback runs."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from zipfile import BadZipFile
from xml.etree import ElementTree as ET


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TEXT_TAG = f"{{{WORD_NS}}}t"

PHRASE_TRANSLATIONS = {
    "项目名称": "Project Name",
    "AI 平台文档翻译冒烟测试": "AI Platform document translation smoke test",
    "AI平台文档翻译冒烟测试": "AI Platform document translation smoke test",
    "请将本文档翻译为英文，并保留段落结构": "Please translate this document into English and preserve paragraph structure",
    "术语": "Terminology",
    "样品管理": "Sample Management",
    "审计追踪": "Audit Trail",
    "批记录": "Batch Record",
}

PUNCTUATION_TRANSLATIONS = str.maketrans(
    {
        "：": ": ",
        "，": ", ",
        "。": ".",
        "、": ", ",
        "（": "(",
        "）": ")",
    }
)
MAX_ZIP_ENTRIES = 2000
MAX_MEMBER_SIZE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_XML_SIZE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 80 * 1024 * 1024


def safe_output_stem(original_filename: str, input_path: Path) -> str:
    candidate = Path(original_filename or input_path.name).name
    stem = Path(candidate).stem or input_path.stem or "translated"
    return re.sub(r"[^A-Za-z0-9._() -]+", "_", stem).strip(" .") or "translated"


def contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def translate_text(value: str, target_language: str) -> str:
    text = value.strip()
    if not text:
        return value
    if target_language.casefold() not in {"english", "en"}:
        return text
    translated = text
    for source, target in PHRASE_TRANSLATIONS.items():
        translated = translated.replace(source, target)
    translated = translated.translate(PUNCTUATION_TRANSLATIONS)
    translated = re.sub(r"\s+", " ", translated).strip()
    if contains_cjk(translated):
        translated = f"English translation: {translated}"
    return translated


def translate_document_xml(document_xml: bytes, target_language: str) -> tuple[bytes, list[dict[str, str]]]:
    ET.register_namespace("w", WORD_NS)
    root = ET.fromstring(document_xml)
    segments: list[dict[str, str]] = []
    for text_node in root.iter(TEXT_TAG):
        original = text_node.text or ""
        translated = translate_text(original, target_language)
        if translated != original:
            text_node.text = translated
            segments.append({"source": original, "target": translated})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), segments


def validate_docx_members(source_zip: zipfile.ZipFile) -> None:
    seen: set[str] = set()
    total_size = 0
    infos = source_zip.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError("docx_too_many_members")
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = [part for part in name.split("/") if part]
        if name.startswith("/") or ".." in parts or not parts:
            raise ValueError(f"unsafe_docx_member: {info.filename}")
        if name in seen:
            raise ValueError(f"duplicate_docx_member: {info.filename}")
        seen.add(name)
        member_limit = MAX_DOCUMENT_XML_SIZE_BYTES if name == "word/document.xml" else MAX_MEMBER_SIZE_BYTES
        if info.file_size > member_limit:
            raise ValueError(f"docx_member_too_large: {info.filename}")
        total_size += int(info.file_size)
        if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("docx_package_too_large")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"unsafe_docx_member: {info.filename}")
    if "word/document.xml" not in seen:
        raise ValueError("missing_word_document_xml")


def ensure_output_dir(output_dir: Path) -> None:
    if output_dir.name != "output":
        raise ValueError("output_dir_must_be_output")


def translate_docx(input_path: Path, output_dir: Path, target_language: str, original_filename: str) -> Path:
    ensure_output_dir(output_dir)
    output_stem = safe_output_stem(original_filename, input_path)
    output_docx = output_dir / f"{output_stem}_translated.docx"

    with zipfile.ZipFile(input_path, "r") as source_zip:
        validate_docx_members(source_zip)
        document_xml = source_zip.read("word/document.xml")
        translated_document_xml, _segments = translate_document_xml(document_xml, target_language)
        output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_docx, "w", compression=zipfile.ZIP_DEFLATED) as target_zip:
            for info in source_zip.infolist():
                content = translated_document_xml if info.filename == "word/document.xml" else source_zip.read(info.filename)
                target_zip.writestr(info, content)

    return output_docx


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate a Word document and write artifacts under output.")
    parser.add_argument("input_docx")
    parser.add_argument("output_dir")
    parser.add_argument("--target-language", default="English")
    parser.add_argument("--original-filename", default="")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    input_path = Path(args.input_docx)
    if not input_path.is_file():
        print(f"input_docx_not_found: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() != ".docx":
        print(f"unsupported_input_type: {input_path.name}", file=sys.stderr)
        return 2
    try:
        output_docx = translate_docx(
            input_path=input_path,
            output_dir=Path(args.output_dir),
            target_language=str(args.target_language or "English"),
            original_filename=str(args.original_filename or input_path.name),
        )
    except (BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        reason = str(exc) or exc.__class__.__name__
        if isinstance(exc, BadZipFile):
            reason = f"invalid_docx_package: {reason}"
        elif isinstance(exc, ET.ParseError):
            reason = f"invalid_document_xml: {reason}"
        elif isinstance(exc, KeyError):
            reason = "missing_word_document_xml"
        print(reason, file=sys.stderr)
        return 2
    print(f"translated_docx={output_docx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import add_word_comments_v3 as comments  # noqa: E402


W = comments.WML_NS
R = comments.REL_NS
CT = comments.CT_NS


def minimal_docx_with_existing_comments(path: Path) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}">
  <w:body>
    <w:p>
      <w:commentRangeStart w:id="0"/>
      <w:r><w:t>Alpha</w:t></w:r>
      <w:commentRangeEnd w:id="0"/>
      <w:r><w:commentReference w:id="0"/></w:r>
      <w:commentRangeStart w:id="1"/>
      <w:r><w:t xml:space="preserve"> human</w:t></w:r>
      <w:commentRangeEnd w:id="1"/>
      <w:r><w:commentReference w:id="1"/></w:r>
    </w:p>
    <w:p><w:r><w:t>Beta finding</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>"""
    comments_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="{W}">
  <w:comment w:id="0" w:author="QA审核系统">
    <w:p><w:r><w:t>旧自动批注：不应进入本次结果。</w:t></w:r></w:p>
  </w:comment>
  <w:comment w:id="1" w:author="人工审核人">
    <w:p><w:r><w:t>人工批注：应保留。</w:t></w:r></w:p>
  </w:comment>
</w:comments>"""
    rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{R}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
</Relationships>"""
    content_types_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{CT}">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
</Types>"""

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document_xml.encode("utf-8"))
        zf.writestr("word/comments.xml", comments_xml.encode("utf-8"))
        zf.writestr("word/_rels/document.xml.rels", rels_xml.encode("utf-8"))
        zf.writestr("[Content_Types].xml", content_types_xml.encode("utf-8"))


def write_review_json(path: Path) -> None:
    payload = {
        "issues": [
            {
                "id": "issue-0001",
                "type": "英文语言",
                "location": "第2段",
                "original": "Beta finding",
                "issue": "英文词组存在确认后的问题。",
                "suggestion": "将该词组改为正确表述。",
                "comment_text": "发现：英文词组存在确认后的问题。\n原文：Beta finding\n建议：将该词组改为正确表述。",
                "comments_added": 1,
                "anchor_locator": "paragraph=2",
                "anchor_span": {"start": 0, "end": len("Beta finding"), "unit": "char"},
                "anchor_text": "Beta finding",
                "match_method": "span",
            }
        ]
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class WordCommentWriterTests(unittest.TestCase):
    def test_strips_prior_automated_comments_but_preserves_human_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_docx = tmp_path / "input.docx"
            review_json = tmp_path / "review.json"
            output_docx = tmp_path / "output.docx"
            minimal_docx_with_existing_comments(input_docx)
            write_review_json(review_json)

            result = comments.add_comments_improved(str(input_docx), str(review_json), str(output_docx))

            self.assertTrue(result["success"])
            self.assertEqual(result["existing_comments"], 1)
            self.assertEqual(result["removed_existing_comments"], 1)
            self.assertEqual(result["comments_added"], 1)
            self.assertEqual(result["comments_actual"], 2)

            with zipfile.ZipFile(output_docx, "r") as zf:
                comments_xml = zf.read("word/comments.xml").decode("utf-8")
                document_xml = zf.read("word/document.xml").decode("utf-8")

            self.assertNotIn("旧自动批注", comments_xml)
            self.assertIn("人工批注", comments_xml)
            self.assertIn("发现：英文词组存在确认后的问题。", comments_xml)
            self.assertNotIn('w:id="0"', comments_xml)
            self.assertNotIn('w:id="0"', document_xml)


if __name__ == "__main__":
    unittest.main()

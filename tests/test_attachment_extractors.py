from __future__ import annotations

import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.attachment_extractors import attachment_capabilities, extract_attachment


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _minimal_docx(text: str) -> bytes:
    xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buffer.getvalue()


def _minimal_xlsx() -> bytes:
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>Name</t></si>
  <si><t>Amount</t></si>
  <si><t>Alpha</t></si>
</sst>
"""
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>42</v></c></row>
  </sheetData>
</worksheet>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def _minimal_pptx(text: str) -> bytes:
    slide = f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", slide)
    return buffer.getvalue()


class AttachmentExtractorTests(unittest.TestCase):
    def test_inline_json_is_pretty_printed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_attachment(
                {"name": "sample.json", "text": "{\"alpha\": 1}"},
                index=1,
                workspace_root=Path(tmp),
            )

        self.assertIn("JSON document", result.text)
        self.assertIn('"alpha": 1', result.text)

    def test_inline_csv_is_rendered_as_table_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_attachment(
                {"name": "sample.csv", "text": "name,amount\nAlpha,42\n"},
                index=1,
                workspace_root=Path(tmp),
            )

        self.assertIn("Delimited table preview", result.text)
        self.assertIn("Alpha\t42", result.text)

    def test_inline_base64_docx_extracts_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_attachment(
                {
                    "name": "memo.docx",
                    "extension": ".docx",
                    "data_base64": _b64(_minimal_docx("Hello from DOCX")),
                },
                index=1,
                workspace_root=Path(tmp),
            )

        self.assertIn("Hello from DOCX", result.text)
        self.assertEqual(result.source, "inline_base64")

    def test_inline_base64_xlsx_extracts_sheet_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_attachment(
                {
                    "name": "book.xlsx",
                    "extension": ".xlsx",
                    "data_base64": _b64(_minimal_xlsx()),
                },
                index=1,
                workspace_root=Path(tmp),
            )

        self.assertIn("Sheet: Data", result.text)
        self.assertIn("Name\tAmount", result.text)
        self.assertIn("Alpha\t42", result.text)

    def test_inline_base64_pptx_extracts_slide_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_attachment(
                {
                    "name": "deck.pptx",
                    "extension": ".pptx",
                    "data_base64": _b64(_minimal_pptx("Quarterly Review")),
                },
                index=1,
                workspace_root=Path(tmp),
            )

        self.assertIn("Slide 1", result.text)
        self.assertIn("Quarterly Review", result.text)

    def test_legacy_doc_is_explicitly_not_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract_attachment(
                {"name": "old.doc", "data_base64": _b64(b"not-a-docx")},
                index=1,
                workspace_root=Path(tmp),
            )

        self.assertIn("Legacy .doc", result.text)
        self.assertIn("Unsupported", result.warning)

    def test_capabilities_exclude_pdf_for_now(self) -> None:
        caps = attachment_capabilities()
        self.assertIn(".docx", caps["accepted_extensions"])
        self.assertIn(".xlsx", caps["accepted_extensions"])
        self.assertIn(".pptx", caps["accepted_extensions"])
        self.assertNotIn(".pdf", caps["accepted_extensions"])
        self.assertIn(".pdf", caps["unsupported_extensions"])


if __name__ == "__main__":
    unittest.main()

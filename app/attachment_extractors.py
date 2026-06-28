from __future__ import annotations

import base64
import binascii
import csv
import html.parser
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree


MAX_INLINE_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_EXTRACTED_CHARS = 60000
MAX_TABLE_ROWS = 80
MAX_TABLE_COLS = 30
MAX_XLSX_SHEETS = 12
PDF_MAX_PAGES = 6
PDF_MIN_TEXT_CHARS = 200
PDF_RENDER_DPI = 180
PDF_MAX_RENDERED_PAGES = 6

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".log",
    ".ini",
    ".cfg",
    ".toml",
    ".yaml",
    ".yml",
    ".sql",
}
TABLE_TEXT_EXTENSIONS = {".csv", ".tsv"}
STRUCTURED_TEXT_EXTENSIONS = {".json", ".xml", ".html", ".htm", ".rtf"}
OFFICE_OPEN_XML_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
SUPPORTED_ATTACHMENT_EXTENSIONS = sorted(
    TEXT_EXTENSIONS
    | TABLE_TEXT_EXTENSIONS
    | STRUCTURED_TEXT_EXTENSIONS
    | OFFICE_OPEN_XML_EXTENSIONS
    | PDF_EXTENSIONS
    | IMAGE_EXTENSIONS
)
UNSUPPORTED_ATTACHMENT_EXTENSIONS = {
    ".doc": "Legacy .doc is a binary Word format. Save as .docx for standard-library extraction.",
    ".xls": "Legacy .xls is a binary Excel format. Save as .xlsx or CSV for standard-library extraction.",
    ".ppt": "Legacy .ppt is a binary PowerPoint format. Save as .pptx for standard-library extraction.",
}


@dataclass(frozen=True)
class AttachmentExtraction:
    name: str
    extension: str
    media_type: str
    source: str
    text: str
    warning: str = ""
    media_parts: List[Dict[str, Any]] = field(default_factory=list)

    def render(self, index: int) -> str:
        header = (
            f"[Attachment {index}: {self.name or 'unnamed attachment'}, "
            f"extension={self.extension or 'unknown'}, "
            f"media_type={self.media_type or 'unknown'}, source={self.source}]"
        )
        if self.warning:
            header += f"\n[Warning: {self.warning}]"
        if self.media_parts:
            header += f"\n[Media parts prepared for FredAI vision: {len(self.media_parts)}]"
        return f"{header}\n{self.text}".strip()


def attachment_capabilities() -> Dict[str, Any]:
    return {
        "inline_base64": True,
        "max_inline_bytes": MAX_INLINE_ATTACHMENT_BYTES,
        "accepted_extensions": SUPPORTED_ATTACHMENT_EXTENSIONS,
        "unsupported_extensions": UNSUPPORTED_ATTACHMENT_EXTENSIONS,
        "notes": [
            "Images are forwarded to FredAI as OpenAI-compatible image_url content parts.",
            "PDFs use optional text extraction first and optional PyMuPDF page-image rendering for scanned or image-heavy pages.",
            "Install pypdf for PDF text extraction and PyMuPDF for PDF page rendering on the work computer.",
            "Legacy .doc and .xls require external parsers; save as .docx, .xlsx, or CSV.",
        ],
    }


def extract_attachment(
    attachment: Dict[str, Any],
    *,
    index: int,
    workspace_root: Path,
) -> AttachmentExtraction:
    name = _attachment_name(attachment, index=index)
    extension = _attachment_extension(attachment, name)
    media_type = _attachment_media_type(attachment)

    inline_text = str(attachment.get("text") or "")
    if inline_text.strip():
        return _extract_from_text(
            inline_text,
            name=name,
            extension=extension,
            media_type=media_type,
            source="inline_text",
        )

    data_base64 = str(attachment.get("data_base64") or "").strip()
    if data_base64:
        return _extract_from_base64(
            data_base64,
            name=name,
            extension=extension,
            media_type=media_type,
        )

    path_text = str(attachment.get("path") or "").strip()
    if path_text:
        return _extract_from_workspace_path(
            path_text,
            name=name,
            media_type=media_type,
            workspace_root=workspace_root,
        )

    note = json.dumps(_safe_attachment_metadata(attachment), ensure_ascii=False, indent=2)
    return AttachmentExtraction(
        name=name,
        extension=extension,
        media_type=media_type,
        source="metadata_only",
        text=f"No file text or bytes were supplied. Attachment metadata:\n{note}",
        warning="Backend received metadata only; no document content was available to parse.",
    )


def _attachment_name(attachment: Dict[str, Any], *, index: int) -> str:
    value = str(attachment.get("name") or attachment.get("filename") or "").strip()
    return value or f"attachment_{index}"


def _attachment_extension(attachment: Dict[str, Any], name: str) -> str:
    raw = str(attachment.get("extension") or "").strip().lower()
    if raw and not raw.startswith("."):
        raw = f".{raw}"
    return raw or Path(name).suffix.lower()


def _attachment_media_type(attachment: Dict[str, Any]) -> str:
    return str(
        attachment.get("media_type")
        or attachment.get("content_type")
        or attachment.get("mime_type")
        or attachment.get("type")
        or ""
    ).strip()


def _safe_attachment_metadata(attachment: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in attachment.items():
        if key in {"data_base64", "text"}:
            safe[key] = f"[{len(str(value))} chars omitted]"
        else:
            safe[key] = value
    return safe


def _extract_from_base64(
    data_base64: str,
    *,
    name: str,
    extension: str,
    media_type: str,
) -> AttachmentExtraction:
    try:
        if "," in data_base64 and data_base64.lower().startswith("data:"):
            data_base64 = data_base64.split(",", 1)[1]
        raw = base64.b64decode(data_base64, validate=False)
    except (binascii.Error, ValueError) as exc:
        return AttachmentExtraction(
            name=name,
            extension=extension,
            media_type=media_type,
            source="inline_base64",
            text="The attachment could not be decoded.",
            warning=f"Invalid base64 payload: {exc}",
        )

    if len(raw) > MAX_INLINE_ATTACHMENT_BYTES:
        return AttachmentExtraction(
            name=name,
            extension=extension,
            media_type=media_type,
            source="inline_base64",
            text=f"Attachment is {len(raw)} bytes, above the configured inline limit.",
            warning="File was not parsed because it is too large for inline upload.",
        )
    return _extract_from_bytes(
        raw,
        name=name,
        extension=extension,
        media_type=media_type,
        source="inline_base64",
    )


def _extract_from_workspace_path(
    path_text: str,
    *,
    name: str,
    media_type: str,
    workspace_root: Path,
) -> AttachmentExtraction:
    raw_path = Path(path_text)
    candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    try:
        path = candidate.resolve()
    except OSError as exc:
        return AttachmentExtraction(
            name=name,
            extension=Path(path_text).suffix.lower(),
            media_type=media_type,
            source="workspace_path",
            text=f"Invalid path: {path_text}",
            warning=str(exc),
        )

    if path != workspace_root and workspace_root not in path.parents:
        return AttachmentExtraction(
            name=name or path.name,
            extension=path.suffix.lower(),
            media_type=media_type,
            source="workspace_path",
            text=f"Path outside workspace root omitted: {path_text}",
            warning="Workspace path attachment was blocked by path safety checks.",
        )
    if not path.exists() or not path.is_file():
        return AttachmentExtraction(
            name=name or path.name,
            extension=path.suffix.lower(),
            media_type=media_type,
            source="workspace_path",
            text=f"File not found: {path_text}",
            warning="Workspace path attachment does not exist.",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return AttachmentExtraction(
            name=path.name,
            extension=path.suffix.lower(),
            media_type=media_type,
            source="workspace_path",
            text=f"Could not read file: {path_text}",
            warning=str(exc),
        )

    return _extract_from_bytes(
        raw,
        name=path.name,
        extension=path.suffix.lower(),
        media_type=media_type,
        source=f"workspace_path:{path}",
    )


def _extract_from_bytes(
    raw: bytes,
    *,
    name: str,
    extension: str,
    media_type: str,
    source: str,
) -> AttachmentExtraction:
    normalized_media_type = _normalize_media_type(media_type)
    if extension in IMAGE_EXTENSIONS or normalized_media_type.startswith("image/"):
        return _extract_image(
            raw,
            name=name,
            extension=extension,
            media_type=media_type,
            source=source,
        )

    if extension in PDF_EXTENSIONS or normalized_media_type == "application/pdf":
        return _extract_pdf(
            raw,
            name=name,
            extension=extension or ".pdf",
            media_type=media_type or "application/pdf",
            source=source,
        )

    unsupported = UNSUPPORTED_ATTACHMENT_EXTENSIONS.get(extension)
    if unsupported:
        return AttachmentExtraction(
            name=name,
            extension=extension,
            media_type=media_type,
            source=source,
            text=unsupported,
            warning="Unsupported file type for this implementation step.",
        )

    if extension == ".docx":
        text, warning = _extract_docx(raw)
    elif extension == ".xlsx":
        text, warning = _extract_xlsx(raw)
    elif extension == ".pptx":
        text, warning = _extract_pptx(raw)
    else:
        decoded, decode_warning = _decode_text(raw)
        extraction = _extract_from_text(
            decoded,
            name=name,
            extension=extension,
            media_type=media_type,
            source=source,
        )
        warning = " ".join(part for part in [decode_warning, extraction.warning] if part)
        return AttachmentExtraction(
            name=name,
            extension=extension,
            media_type=media_type,
            source=source,
            text=extraction.text,
            warning=warning,
        )

    text, truncate_warning = _truncate(text, MAX_EXTRACTED_CHARS)
    return AttachmentExtraction(
        name=name,
        extension=extension,
        media_type=media_type,
        source=source,
        text=text,
        warning=" ".join(part for part in [warning, truncate_warning] if part),
    )


def _normalize_media_type(media_type: str) -> str:
    return media_type.split(";", 1)[0].strip().lower()


def _image_media_type(extension: str, media_type: str) -> str:
    normalized = _normalize_media_type(media_type)
    if normalized.startswith("image/"):
        return normalized
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(extension, "image/png")


def _extract_image(
    raw: bytes,
    *,
    name: str,
    extension: str,
    media_type: str,
    source: str,
) -> AttachmentExtraction:
    clean_media_type = _image_media_type(extension, media_type)
    data_url = f"data:{clean_media_type};base64,{base64.b64encode(raw).decode('ascii')}"
    return AttachmentExtraction(
        name=name,
        extension=extension,
        media_type=clean_media_type,
        source=source,
        text=(
            "Image attachment prepared for FredAI vision. The image bytes were passed as an "
            "OpenAI-compatible image_url content part."
        ),
        media_parts=[{"type": "image_url", "image_url": {"url": data_url}}],
    )


def _extract_pdf(
    raw: bytes,
    *,
    name: str,
    extension: str,
    media_type: str,
    source: str,
) -> AttachmentExtraction:
    text, text_warning = _extract_pdf_text(raw, max_pages=PDF_MAX_PAGES)
    has_images, image_warning = _pdf_has_images(raw)
    should_render = bool(has_images) or len(text.strip()) < PDF_MIN_TEXT_CHARS
    media_parts: List[Dict[str, Any]] = []
    render_warning = ""

    if should_render:
        media_parts, render_warning = _render_pdf_pages(
            raw,
            max_pages=PDF_MAX_RENDERED_PAGES,
            dpi=PDF_RENDER_DPI,
        )

    lines = [
        "PDF attachment received.",
        f"Text chars extracted: {len(text.strip())}",
        f"PDF has embedded images: {has_images if has_images is not None else 'unknown'}",
        f"Rendered page images for FredAI vision: {len(media_parts)}",
        "",
    ]
    if text.strip():
        lines.extend(["Extracted PDF text:", text.strip()])
    elif not media_parts:
        lines.append(
            "No PDF text or page images could be extracted. Install pypdf for text extraction "
            "and PyMuPDF for image/scanned-page rendering."
        )

    rendered_text, truncate_warning = _truncate("\n".join(lines), MAX_EXTRACTED_CHARS)
    warning = " ".join(
        part for part in [text_warning, image_warning, render_warning, truncate_warning] if part
    )
    return AttachmentExtraction(
        name=name,
        extension=extension,
        media_type=media_type,
        source=source,
        text=rendered_text,
        warning=warning,
        media_parts=media_parts,
    )


def _extract_pdf_text(raw: bytes, *, max_pages: int) -> tuple[str, str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        return "", f"pypdf is not installed, so direct PDF text extraction was skipped: {exc}"

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        return "", f"PDF text parser warning: {exc}"

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            return "", "PDF is encrypted and could not be opened without a password."

    parts: List[str] = []
    page_count = len(reader.pages)
    total = min(page_count, max_pages)
    for index in range(total):
        try:
            page_text = (reader.pages[index].extract_text() or "").strip()
        except Exception as exc:
            parts.append(f"--- Page {index + 1} ---\n[Text extraction failed: {exc}]")
            continue
        if page_text:
            parts.append(f"--- Page {index + 1} ---\n{page_text}")

    warning = ""
    if page_count > max_pages:
        warning = f"Only first {max_pages} PDF pages were parsed for text."
    return "\n\n".join(parts).strip(), warning


def _load_fitz():
    try:
        import fitz  # type: ignore
    except Exception as exc:
        return None, exc
    return fitz, None


def _pdf_has_images(raw: bytes) -> tuple[Optional[bool], str]:
    fitz, error = _load_fitz()
    if fitz is None:
        return None, f"PyMuPDF is not installed, so PDF image detection was skipped: {error}"
    try:
        with fitz.open(stream=raw, filetype="pdf") as doc:
            for page in doc:
                if page.get_images(full=True):
                    return True, ""
    except Exception as exc:
        return None, f"PDF image detection warning: {exc}"
    return False, ""


def _render_pdf_pages(raw: bytes, *, max_pages: int, dpi: int) -> tuple[List[Dict[str, Any]], str]:
    fitz, error = _load_fitz()
    if fitz is None:
        return [], f"PyMuPDF is not installed, so PDF page rendering was skipped: {error}"

    parts: List[Dict[str, Any]] = []
    try:
        with fitz.open(stream=raw, filetype="pdf") as doc:
            total = min(len(doc), max_pages)
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            for index in range(total):
                page = doc.load_page(index)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                png = pix.tobytes("png")
                data_url = f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
            warning = ""
            if len(doc) > max_pages:
                warning = f"Only first {max_pages} PDF pages were rendered for vision."
            return parts, warning
    except Exception as exc:
        return [], f"PDF page rendering warning: {exc}"


def _extract_from_text(
    text: str,
    *,
    name: str,
    extension: str,
    media_type: str,
    source: str,
) -> AttachmentExtraction:
    warning = ""
    if extension in TABLE_TEXT_EXTENSIONS:
        delimiter = "\t" if extension == ".tsv" else ","
        extracted, warning = _extract_delimited_text(text, delimiter=delimiter)
    elif extension == ".json" or media_type == "application/json":
        extracted, warning = _extract_json_text(text)
    elif extension == ".xml" or media_type in {"application/xml", "text/xml"}:
        extracted, warning = _extract_xml_text(text)
    elif extension in {".html", ".htm"} or media_type == "text/html":
        extracted, warning = _extract_html_text(text)
    elif extension == ".rtf":
        extracted = _extract_rtf_text(text)
    else:
        extracted = text

    extracted, truncate_warning = _truncate(extracted, MAX_EXTRACTED_CHARS)
    warning = " ".join(part for part in [warning, truncate_warning] if part)
    return AttachmentExtraction(
        name=name,
        extension=extension,
        media_type=media_type,
        source=source,
        text=extracted,
        warning=warning,
    )


def _decode_text(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding), "" if encoding.startswith("utf") else f"Decoded using {encoding}."
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "Decoded with replacement characters."


def _truncate(text: str, limit: int) -> tuple[str, str]:
    if len(text) <= limit:
        return text, ""
    omitted = len(text) - limit
    return text[:limit].rstrip() + f"\n\n[Attachment text truncated; {omitted} chars omitted.]", (
        f"Extraction was truncated to {limit} chars."
    )


def _extract_delimited_text(text: str, *, delimiter: str) -> tuple[str, str]:
    stream = io.StringIO(text)
    reader = csv.reader(stream, delimiter=delimiter)
    rows: List[List[str]] = []
    warning = ""
    try:
        for row_index, row in enumerate(reader):
            if row_index >= MAX_TABLE_ROWS:
                warning = f"Only first {MAX_TABLE_ROWS} rows were included."
                break
            rows.append(row[:MAX_TABLE_COLS])
    except csv.Error as exc:
        return text, f"CSV parser warning: {exc}; raw text was used."

    if not rows:
        return "[Delimited file is empty.]", warning
    max_cols = max(len(row) for row in rows)
    rendered = ["Delimited table preview:"]
    rendered.append(f"Rows shown: {len(rows)}")
    rendered.append(f"Max columns shown: {max_cols}")
    rendered.append("")
    rendered.extend(_render_rows(rows))
    return "\n".join(rendered), warning


def _extract_json_text(text: str) -> tuple[str, str]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return text, f"JSON parser warning: {exc}; raw text was used."
    shape = "array" if isinstance(parsed, list) else type(parsed).__name__
    pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    return f"JSON document ({shape}):\n{pretty}", ""


def _extract_xml_text(text: str) -> tuple[str, str]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        return text, f"XML parser warning: {exc}; raw text was used."
    text_content = " ".join(piece.strip() for piece in root.itertext() if piece.strip())
    if not text_content:
        text_content = "[XML parsed successfully but contained no text nodes.]"
    return f"XML document root: {_strip_ns(root.tag)}\n{text_content}", ""


class _HTMLTextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)


def _extract_html_text(text: str) -> tuple[str, str]:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(text)
    except Exception as exc:
        return text, f"HTML parser warning: {exc}; raw text was used."
    extracted = "\n".join(parser.parts).strip()
    return extracted or "[HTML contained no readable text.]", ""


def _extract_rtf_text(text: str) -> str:
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\d* ?", " ", text)
    text = re.sub(r"[{}]", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_docx(raw: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as docx:
            document_xml = docx.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        return "Could not read DOCX content.", f"DOCX parser warning: {exc}"

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        return "Could not parse DOCX XML.", f"DOCX XML parser warning: {exc}"

    body = root.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body")
    elements = list(body) if body is not None else list(root)
    parts: List[str] = []
    for element in elements:
        tag = _strip_ns(element.tag)
        if tag == "p":
            text = _word_text(element)
            if text:
                parts.append(text)
        elif tag == "tbl":
            table = _word_table(element)
            if table:
                parts.append(table)

    extracted = "\n\n".join(parts).strip()
    return extracted or "[DOCX parsed successfully but contained no readable text.]", ""


def _word_table(table: ElementTree.Element) -> str:
    rows: List[List[str]] = []
    for tr in table.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr"):
        row: List[str] = []
        for tc in tr.findall("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc"):
            cell_text = _word_text(tc)
            row.append(" ".join(cell_text.split()))
        if row:
            rows.append(row)
    if not rows:
        return ""
    return "DOCX table:\n" + "\n".join(_render_rows(rows))


def _word_text(element: ElementTree.Element) -> str:
    parts: List[str] = []
    for node in element.iter():
        tag = _strip_ns(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _extract_xlsx(raw: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as workbook:
            shared_strings = _xlsx_shared_strings(workbook)
            sheets = _xlsx_sheet_paths(workbook)
            rendered: List[str] = []
            for sheet_index, (sheet_name, sheet_path) in enumerate(sheets, start=1):
                if sheet_index > MAX_XLSX_SHEETS:
                    rendered.append(f"[Only first {MAX_XLSX_SHEETS} sheets were included.]")
                    break
                try:
                    sheet_xml = workbook.read(sheet_path)
                except KeyError:
                    continue
                rows = _xlsx_rows(sheet_xml, shared_strings)
                rendered.append(f"Sheet: {sheet_name}")
                rendered.extend(_render_rows(rows[:MAX_TABLE_ROWS]) or ["[Sheet has no readable cell values.]"])
                if len(rows) > MAX_TABLE_ROWS:
                    rendered.append(f"[Only first {MAX_TABLE_ROWS} rows were included.]")
                rendered.append("")
    except (zipfile.BadZipFile, OSError) as exc:
        return "Could not read XLSX content.", f"XLSX parser warning: {exc}"

    text = "\n".join(rendered).strip()
    return text or "[XLSX parsed successfully but contained no readable text.]", ""


def _extract_pptx(raw: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as deck:
            slide_names = sorted(
                (name for name in deck.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=_natural_key,
            )
            rendered: List[str] = []
            for slide_index, slide_name in enumerate(slide_names, start=1):
                slide_xml = deck.read(slide_name)
                root = ElementTree.fromstring(slide_xml)
                texts = [
                    node.text.strip()
                    for node in root.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}t")
                    if node.text and node.text.strip()
                ]
                if texts:
                    rendered.append(f"Slide {slide_index}:")
                    rendered.extend(texts)
                    rendered.append("")
    except (zipfile.BadZipFile, ElementTree.ParseError, OSError) as exc:
        return "Could not read PPTX content.", f"PPTX parser warning: {exc}"

    text = "\n".join(rendered).strip()
    return text or "[PPTX parsed successfully but contained no readable text.]", ""


def _xlsx_shared_strings(workbook: zipfile.ZipFile) -> List[str]:
    try:
        xml = workbook.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(xml)
    strings: List[str] = []
    for si in root.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
        strings.append("".join(t.text or "" for t in si.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    return strings


def _xlsx_sheet_paths(workbook: zipfile.ZipFile) -> List[tuple[str, str]]:
    try:
        workbook_xml = workbook.read("xl/workbook.xml")
        rels_xml = workbook.read("xl/_rels/workbook.xml.rels")
    except KeyError:
        return [("Sheet1", "xl/worksheets/sheet1.xml")]

    rel_root = ElementTree.fromstring(rels_xml)
    rels: Dict[str, str] = {}
    for rel in rel_root:
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rels[rel_id] = "xl/" + target.lstrip("/")

    wb_root = ElementTree.fromstring(workbook_xml)
    sheets: List[tuple[str, str]] = []
    for sheet in wb_root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"):
        name = sheet.attrib.get("name") or f"Sheet{len(sheets) + 1}"
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        if rel_id and rel_id in rels:
            sheets.append((name, rels[rel_id]))
    return sheets or [("Sheet1", "xl/worksheets/sheet1.xml")]


def _xlsx_rows(sheet_xml: bytes, shared_strings: List[str]) -> List[List[str]]:
    root = ElementTree.fromstring(sheet_xml)
    rows: List[List[str]] = []
    for row_el in root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
        values: Dict[int, str] = {}
        for cell in row_el.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
            ref = cell.attrib.get("r", "")
            column = _xlsx_column_index(ref)
            if column >= MAX_TABLE_COLS:
                continue
            value = _xlsx_cell_value(cell, shared_strings)
            values[column] = value
        if values:
            max_col = min(max(values) + 1, MAX_TABLE_COLS)
            rows.append([values.get(col, "") for col in range(max_col)])
    return rows


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: List[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))

    value_el = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
    value = value_el.text if value_el is not None and value_el.text is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError):
            return value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _xlsx_column_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    if not letters:
        return 0
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return max(0, value - 1)


def _render_rows(rows: Iterable[Iterable[str]]) -> List[str]:
    return ["\t".join(str(cell).replace("\n", " ").strip() for cell in row) for row in rows]


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _natural_key(value: str) -> List[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]

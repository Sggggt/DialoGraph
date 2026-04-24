from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


@dataclass
class ParsedSection:
    title: str
    text: str
    page_number: int | None = None
    section: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def detect_source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".ppt", ".pptx"}:
        return suffix.lstrip(".")
    if suffix == ".docx":
        return "docx"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".txt", ".tex"}:
        return "text"
    if suffix in {".png", ".jpg", ".jpeg", ".bmp"}:
        return "image"
    if suffix == ".ipynb":
        return "notebook"
    if suffix == ".html":
        return "html"
    return "unknown"


def load_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def parse_markdown(path: Path) -> list[ParsedSection]:
    text = load_text(path)
    lines = text.splitlines()
    sections: list[ParsedSection] = []
    current_title = path.stem
    buffer: list[str] = []
    for line in lines:
        if line.strip().startswith("#"):
            if buffer:
                sections.append(
                    ParsedSection(
                        title=current_title,
                        text="\n".join(buffer).strip(),
                        section=current_title,
                        metadata={"content_kind": "markdown"},
                    )
                )
                buffer = []
            current_title = line.lstrip("#").strip() or current_title
            continue
        buffer.append(line)
    if buffer:
        sections.append(
            ParsedSection(
                title=current_title,
                text="\n".join(buffer).strip(),
                section=current_title,
                metadata={"content_kind": "markdown"},
            )
        )
    return [section for section in sections if section.text]


def parse_text(path: Path) -> list[ParsedSection]:
    text = load_text(path)
    return [ParsedSection(title=path.stem, text=text.strip(), section=path.stem, metadata={"content_kind": "text"})]


def parse_html(path: Path) -> list[ParsedSection]:
    html = load_text(path)
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.text.strip() if soup.title and soup.title.text else path.stem
    text = soup.get_text("\n", strip=True)
    return [ParsedSection(title=title, text=text, section=title, metadata={"content_kind": "html"})]


def parse_pdf(path: Path) -> list[ParsedSection]:
    import fitz

    document = fitz.open(path)
    sections: list[ParsedSection] = []
    for idx, page in enumerate(document, start=1):
        text = page.get_text("text").strip()
        if not text:
            continue
        page_title = text.splitlines()[0][:120]
        sections.append(
            ParsedSection(
                title=page_title or f"{path.stem} p.{idx}",
                text=text,
                page_number=idx,
                section=page_title or path.stem,
                metadata={"content_kind": "pdf_page"},
            )
        )
    return sections


def parse_presentation(path: Path) -> list[ParsedSection]:
    from pptx import Presentation

    presentation = Presentation(path)
    sections: list[ParsedSection] = []
    for idx, slide in enumerate(presentation.slides, start=1):
        fragments: list[str] = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                fragments.append(shape.text.strip())
        text = "\n".join(fragment for fragment in fragments if fragment)
        if text:
            lines = text.splitlines()
            sections.append(
                ParsedSection(
                    title=lines[0][:120] if lines else f"{path.stem} slide {idx}",
                    text=text,
                    page_number=idx,
                    section=f"slide-{idx}",
                    metadata={"content_kind": "slide"},
                )
            )
    return sections


def parse_docx(path: Path) -> list[ParsedSection]:
    from docx import Document

    document = Document(path)
    sections: list[ParsedSection] = []
    current_title = path.stem
    buffer: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if paragraph.style and "Heading" in paragraph.style.name:
            if buffer:
                sections.append(
                    ParsedSection(
                        title=current_title,
                        text="\n".join(buffer),
                        section=current_title,
                        metadata={"content_kind": "doc_section"},
                    )
                )
                buffer = []
            current_title = text
            continue
        buffer.append(text)
    if buffer:
        sections.append(
            ParsedSection(
                title=current_title,
                text="\n".join(buffer),
                section=current_title,
                metadata={"content_kind": "doc_section"},
            )
        )
    return sections


def parse_notebook(path: Path) -> list[ParsedSection]:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    sections: list[ParsedSection] = []
    cell_buffer: list[str] = []
    current_title = path.stem
    current_index = 0
    for cell in notebook.get("cells", []):
        current_index += 1
        source = "".join(cell.get("source", []))
        if not source.strip():
            continue
        if cell.get("cell_type") == "markdown":
            heading = next((line.lstrip("# ").strip() for line in source.splitlines() if line.strip().startswith("#")), None)
            if heading:
                if cell_buffer:
                    sections.append(
                        ParsedSection(
                            title=current_title,
                            text="\n".join(cell_buffer),
                            section=current_title,
                            metadata={"cell_index": current_index, "content_kind": "markdown"},
                        )
                    )
                    cell_buffer = []
                current_title = heading
            cell_buffer.append(source.strip())
        else:
            outputs: list[str] = []
            for output in cell.get("outputs", []):
                if "text" in output:
                    outputs.append("".join(output["text"]))
                elif "data" in output and "text/plain" in output["data"]:
                    outputs.append("".join(output["data"]["text/plain"]))
            block = ["[Code Cell]", source.strip()]
            if outputs:
                block.extend(["[Output]", "\n".join(outputs).strip()])
            if cell_buffer:
                sections.append(
                    ParsedSection(
                        title=current_title,
                        text="\n\n".join(cell_buffer),
                        section=current_title,
                        metadata={"cell_index": current_index, "content_kind": "markdown"},
                    )
                )
                cell_buffer = []
            sections.append(
                ParsedSection(
                    title=f"{current_title} code",
                    text="\n".join(part for part in block if part),
                    section=current_title,
                    metadata={"cell_index": current_index, "content_kind": "code"},
                )
            )
    if cell_buffer:
        sections.append(
            ParsedSection(
                title=current_title,
                text="\n\n".join(cell_buffer),
                section=current_title,
                metadata={"content_kind": "markdown"},
            )
        )
    return sections


def parse_image(path: Path) -> list[ParsedSection]:
    try:
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        results = ocr.ocr(str(path), cls=True)
        text = "\n".join(line[1][0] for page in results for line in page)
        if text.strip():
            return [ParsedSection(title=path.stem, text=text.strip(), section=path.stem, metadata={"content_kind": "ocr"})]
    except Exception:
        pass

    try:
        import pytesseract
        from PIL import Image

        image = Image.open(path)
        text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        if text.strip():
            return [ParsedSection(title=path.stem, text=text.strip(), section=path.stem)]
    except Exception as exc:
        raise RuntimeError(f"OCR dependencies unavailable for {path.name}: {exc}") from exc
    raise RuntimeError(f"No OCR text extracted from {path.name}")


def parse_with_unstructured(path: Path) -> list[ParsedSection]:
    from unstructured.partition.auto import partition

    elements = partition(filename=str(path))
    text = "\n".join(str(element) for element in elements if str(element).strip())
    return [ParsedSection(title=path.stem, text=text, section=path.stem, metadata={"content_kind": "unstructured"})] if text else []


def parse_document(path: Path) -> tuple[str, list[ParsedSection]]:
    source_type = detect_source_type(path)
    parsers = {
        "pdf": parse_pdf,
        "ppt": parse_presentation,
        "pptx": parse_presentation,
        "docx": parse_docx,
        "markdown": parse_markdown,
        "text": parse_text,
        "image": parse_image,
        "notebook": parse_notebook,
        "html": parse_html,
    }
    parser = parsers.get(source_type)
    if parser is None:
        return source_type, parse_with_unstructured(path)
    try:
        return source_type, parser(path)
    except Exception:
        return source_type, parse_with_unstructured(path)


def sections_to_json(sections: list[ParsedSection]) -> list[dict[str, Any]]:
    return [asdict(section) for section in sections]


def derive_chapter(path: Path) -> str:
    candidates = [path.stem, path.parent.name]
    for item in candidates:
        match = re.match(r"(L\d+|Lecture\s+\d+|Lab\s+\d+)", item, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return path.parent.name if path.parent.name != path.anchor else path.stem

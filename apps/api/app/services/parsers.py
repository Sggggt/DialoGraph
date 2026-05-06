from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.utils import source_type_from_path


@dataclass
class ParsedSection:
    title: str
    text: str
    page_number: int | None = None
    section: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def detect_source_type(path: Path) -> str:
    return source_type_from_path(path)


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


def _detect_formula(text: str) -> bool:
    """检测文本中是否包含大量数学符号，可能是公式。"""
    formula_chars = set("∑∫√λθπσμ±×÷≤≥≠≈∝∂∇∆∞∈∪∩⊂⊃⟨⟩αβγδεζηικνξορστυφχψω")
    if not text:
        return False
    ratio = sum(1 for c in text if c in formula_chars) / len(text)
    return ratio > 0.03


def parse_pdf(path: Path) -> list[ParsedSection]:
    import fitz

    document = fitz.open(path)
    sections: list[ParsedSection] = []
    for idx, page in enumerate(document, start=1):
        # 优先尝试 markdown 格式以保留表格结构
        try:
            text = page.get_text("markdown").strip()
        except Exception:
            text = page.get_text("text").strip()

        if not text:
            continue

        # 检测表格（Markdown 表格特征：包含 | 和 --- 分隔行）
        lines = text.splitlines()
        has_table = any("|" in line and "---" in line for line in lines)
        has_formula = _detect_formula(text)

        page_title = lines[0][:120] if lines else ""
        metadata: dict[str, Any] = {"content_kind": "pdf_page"}
        if has_table:
            metadata["has_table"] = True
        if has_formula:
            metadata["has_formula"] = True

        sections.append(
            ParsedSection(
                title=page_title or f"{path.stem} p.{idx}",
                text=text,
                page_number=idx,
                section=page_title or path.stem,
                metadata=metadata,
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
        if get_settings().enable_model_fallback:
            return source_type, parse_with_unstructured(path)
        raise RuntimeError(f"Unsupported source type for {path.name}: {source_type}")
    try:
        return source_type, parser(path)
    except Exception as exc:
        if get_settings().enable_model_fallback:
            return source_type, parse_with_unstructured(path)
        raise RuntimeError(f"Failed to parse {path.name} as {source_type}: {exc}") from exc


def sections_to_json(sections: list[ParsedSection]) -> list[dict[str, Any]]:
    return [asdict(section) for section in sections]


INVALID_CHAPTER_LABELS = {
    "data",
    "storage",
    "reviewmarkdown",
}


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def is_date_like_label(value: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{6}", value.strip()))


def is_invalid_chapter_label(value: str | None, course_name: str | None = None) -> bool:
    if not value:
        return True
    normalized = _normalize_label(value)
    if not normalized:
        return True
    if normalized.replace(" ", "") in INVALID_CHAPTER_LABELS:
        return True
    if is_date_like_label(normalized.replace(" ", "")):
        return True
    if course_name and normalized == _normalize_label(course_name):
        return True
    return False


def canonical_chapter_label(value: str, course_name: str | None = None) -> str | None:
    cleaned = re.sub(r"[_-]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    match = re.search(r"\b(?:chapter|chap)\s*\.?\s*(\d+[A-Za-z]?)\b", cleaned, flags=re.IGNORECASE)
    if match:
        return f"Chapter {match.group(1)}"

    match = re.search(r"\b(?:lecture|lec|l)\s*\.?\s*(\d+[A-Za-z]?)\b", cleaned, flags=re.IGNORECASE)
    if match:
        return f"Lecture {match.group(1)}"

    match = re.search(r"\bweek\s*\.?\s*(\d+[A-Za-z]?)\b", cleaned, flags=re.IGNORECASE)
    if match:
        return f"Week {match.group(1)}"

    match = re.search(r"\blab\s*\.?\s*(\d+[A-Za-z]?)\b", cleaned, flags=re.IGNORECASE)
    if match:
        return f"Lab {match.group(1)}"

    if re.search(r"\blabs?\b|\blaboratory\b", cleaned, flags=re.IGNORECASE):
        if re.search(r"\bquestions?\b", cleaned, flags=re.IGNORECASE):
            return "Lab Questions"
        if re.search(r"\bsolutions?\b", cleaned, flags=re.IGNORECASE):
            return "Lab Solutions"
        return "Lab"

    if re.search(r"\bcourse\s*work\b|\bcoursework\b", cleaned, flags=re.IGNORECASE):
        return "Coursework"

    if re.search(r"\bz\s*table\b|\breference\b|\bformula\b|\bsummary\b|\bvisuali[sz]er\b", cleaned, flags=re.IGNORECASE):
        return "Reference"

    if re.search(r"\breview\b|\brevision\b|复习|总讲义", cleaned, flags=re.IGNORECASE):
        return "Review"

    cleaned = cleaned[:80].strip()
    if cleaned and not re.search(r"[A-Za-z0-9]", cleaned):
        return "Reference"
    return None if is_invalid_chapter_label(cleaned, course_name=course_name) else cleaned


def derive_chapter(path: Path, course_name: str | None = None) -> str:
    candidates = [path.stem, path.parent.name]
    for item in candidates:
        label = canonical_chapter_label(item, course_name=course_name)
        if label and not is_invalid_chapter_label(label, course_name=course_name):
            return label
    return path.stem[:80]

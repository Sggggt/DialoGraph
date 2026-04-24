from __future__ import annotations

import re
from collections.abc import Iterable

from app.services.parsers import ParsedSection

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:  # pragma: no cover - optional fallback
    RecursiveCharacterTextSplitter = None


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def rough_token_count(text: str) -> int:
    return max(1, len(text.split()))


def build_splitter(chunk_size: int = 1200, chunk_overlap: int = 150):
    if RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
        )
    return None


def split_text(text: str, chunk_size: int = 1200, chunk_overlap: int = 150) -> list[str]:
    splitter = build_splitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if splitter:
        return [chunk for chunk in splitter.split_text(normalize_text(text)) if chunk.strip()]

    normalized = normalize_text(text)
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end == len(normalized):
            break
        start = max(start + 1, end - chunk_overlap)
    return [chunk for chunk in chunks if chunk]


def infer_content_kind(content: str, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    stripped = content.lstrip()
    if stripped.startswith("[Code Cell]"):
        return "code"
    if stripped.startswith("[Output]"):
        return "output"
    return "text"


def chunk_sections(sections: Iterable[ParsedSection], chapter: str, source_type: str) -> list[dict]:
    chunks: list[dict] = []
    for section_index, section in enumerate(sections, start=1):
        content_kind = infer_content_kind(section.text, section.metadata.get("content_kind") if section.metadata else None)
        unit_size = 900 if content_kind == "code" else 1200
        overlap = 120 if source_type == "markdown" else 150
        for chunk_index, content in enumerate(split_text(section.text, unit_size, overlap), start=1):
            snippet = content[:280].strip()
            chunks.append(
                {
                    "content": content,
                    "snippet": snippet,
                    "chapter": chapter,
                    "section": section.section or section.title,
                    "page_number": section.page_number,
                    "token_count": rough_token_count(content),
                    "metadata": {
                        "section_title": section.title,
                        "section_index": section_index,
                        "chunk_index": chunk_index,
                        "content_kind": content_kind,
                        **section.metadata,
                    },
                }
            )
    return chunks

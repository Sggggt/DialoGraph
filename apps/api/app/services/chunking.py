from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.services.parsers import ParsedSection

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:  # pragma: no cover - optional fallback
    RecursiveCharacterTextSplitter = None


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def sanitize_metadata(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, dict):
        return {normalize_text(key) if isinstance(key, str) else key: sanitize_metadata(item) for key, item in value.items()}
    return value


def rough_token_count(text: str) -> int:
    return max(1, len(text.split()))


DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120
CODE_CHUNK_SIZE = 700
CODE_CHUNK_OVERLAP = 100
EMBEDDING_TEXT_VERSION = "metadata_enriched_v1"
CODE_KEEP_MARKERS = ("centrality", "community", "random network", "configuration model")
MOJIBAKE_MARKERS = ("�", "鈥", "鐩", "绗", "鍥", "灏", "瀛", "凹", "鍫")


def build_splitter(chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP):
    if RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n## ", "\n# ", "\n\n", "\n", ". ", " ", ""],
        )
    return None


def split_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
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


def normalize_for_dedup(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text.lower(), flags=re.UNICODE)).strip()


def mojibake_ratio(text: str) -> float:
    if not text:
        return 0.0
    marker_count = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
    return marker_count / len(text)


def is_toc_like(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < 6:
        return False
    dotted = sum(1 for line in lines if re.search(r"\.{4,}\s*\d+$", line))
    numeric_short = sum(1 for line in lines if re.fullmatch(r"\d+(\.\d+)*", line) or len(line) <= 3)
    return dotted >= 4 or numeric_short / len(lines) > 0.45


def should_keep_code_chunk(section_name: str, section_title: str) -> bool:
    text = f"{section_name} {section_title}".lower()
    return any(marker in text for marker in CODE_KEEP_MARKERS)


def should_keep_chunk(content: str, content_kind: str, section_name: str, section_title: str) -> bool:
    normalized = normalize_for_dedup(content)
    if content_kind == "output":
        return False
    if len(normalized) < 40:
        return False
    if mojibake_ratio(content) > 0.01:
        return False
    if is_toc_like(content):
        return False
    if content_kind == "code" and not should_keep_code_chunk(section_name, section_title):
        return False
    return True


def embedding_text(
    *,
    document_title: str,
    chapter: str | None,
    section: str | None,
    source_type: str | None,
    content_kind: str | None,
    content: str,
) -> str:
    return "\n".join(
        [
            f"Document: {document_title}",
            f"Chapter: {chapter or ''}",
            f"Section: {section or ''}",
            f"Source Type: {source_type or ''}",
            f"Content Kind: {content_kind or ''}",
            "Content:",
            content,
        ]
    )


def chunk_sections_with_stats(sections: Iterable[ParsedSection], chapter: str, source_type: str) -> tuple[list[dict], dict[str, int]]:
    chunks: list[dict] = []
    stats = {"chunks_before_filter": 0, "chunks_filtered": 0}
    for section_index, section in enumerate(sections, start=1):
        section_text = normalize_text(section.text)
        section_title = normalize_text(section.title)
        section_name = normalize_text(section.section or section.title)
        content_kind = infer_content_kind(section_text, section.metadata.get("content_kind") if section.metadata else None)
        unit_size = CODE_CHUNK_SIZE if content_kind == "code" else DEFAULT_CHUNK_SIZE
        overlap = CODE_CHUNK_OVERLAP if content_kind == "code" else DEFAULT_CHUNK_OVERLAP
        for chunk_index, content in enumerate(split_text(section_text, unit_size, overlap), start=1):
            stats["chunks_before_filter"] += 1
            if not should_keep_chunk(content, content_kind, section_name, section_title):
                stats["chunks_filtered"] += 1
                continue
            snippet = content[:280].strip()
            chunks.append(
                {
                    "content": content,
                    "snippet": snippet,
                    "chapter": chapter,
                    "section": section_name,
                    "page_number": section.page_number,
                    "token_count": rough_token_count(content),
                    "metadata": sanitize_metadata({
                        "section_title": section_title,
                        "section_index": section_index,
                        "chunk_index": chunk_index,
                        "content_kind": content_kind,
                        "chunking_strategy": "chunk_800_metadata_enriched_v1",
                        **section.metadata,
                    }),
                }
            )
    return chunks, stats


def chunk_sections(sections: Iterable[ParsedSection], chapter: str, source_type: str) -> list[dict]:
    return chunk_sections_with_stats(sections, chapter, source_type)[0]

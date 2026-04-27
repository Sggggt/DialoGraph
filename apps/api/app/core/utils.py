from __future__ import annotations

from pathlib import Path


SOURCE_TYPE_BY_SUFFIX = {
    ".pdf": "pdf",
    ".ppt": "ppt",
    ".pptx": "pptx",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".tex": "text",
    ".ipynb": "notebook",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".bmp": "image",
    ".html": "html",
    ".htm": "html",
}


def source_type_from_path(path: str | Path) -> str:
    return SOURCE_TYPE_BY_SUFFIX.get(Path(path).suffix.lower(), "unknown")

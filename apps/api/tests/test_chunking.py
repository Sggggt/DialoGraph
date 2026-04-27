from __future__ import annotations

from app.services.chunking import chunk_sections, infer_content_kind, normalize_text, split_text
from app.services.parsers import ParsedSection


def test_normalize_and_split_text_overlap():
    text = "alpha\x00  beta\r\n\r\n\r\n" + ("word " * 80)
    normalized = normalize_text(text)
    assert "\x00" not in normalized
    assert "\r\n" not in normalized
    assert "\n\n\n" not in normalized
    assert "alpha beta" in normalized

    chunks = split_text("0123456789" * 20, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 1
    assert chunks[0][-10:] == chunks[1][:10]


def test_content_kind_and_chunk_metadata():
    sections = [
        ParsedSection(title="Code", text="[Code Cell]\nprint('x')", metadata={}),
        ParsedSection(title="Text\x00", text="Course\x00 note text", metadata={"content_kind": "markdown", "raw": "a\x00b"}),
    ]
    chunks = chunk_sections(sections, chapter="L1", source_type="notebook")

    assert infer_content_kind("[Output]\n1") == "output"
    assert chunks[0]["metadata"]["content_kind"] == "code"
    assert chunks[1]["metadata"]["content_kind"] == "markdown"
    assert "\x00" not in chunks[1]["content"]
    assert chunks[1]["metadata"]["raw"] == "ab"
    assert all(chunk["chapter"] == "L1" for chunk in chunks)

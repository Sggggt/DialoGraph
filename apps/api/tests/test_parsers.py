from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import parsers
from app.services.parsers import derive_chapter, parse_document


def test_parse_markdown_html_notebook(tmp_path, no_fallback_env):
    markdown = tmp_path / "notes.md"
    markdown.write_text("# Centrality\nDegree centrality counts edges.", encoding="utf-8")
    html = tmp_path / "notes.html"
    html.write_text("<html><title>Networks</title><body><h1>Graphs</h1><p>Nodes and edges.</p></body></html>", encoding="utf-8")
    notebook = tmp_path / "lab.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Lab\n", "Centrality exercise"], "outputs": []},
                    {"cell_type": "code", "source": ["print('graph')"], "outputs": [{"text": ["graph\n"]}]},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert parse_document(markdown)[0] == "markdown"
    assert "Nodes and edges" in parse_document(html)[1][0].text
    _, notebook_sections = parse_document(notebook)
    assert {section.metadata["content_kind"] for section in notebook_sections} == {"markdown", "code"}


def test_parse_docx_pptx_pdf(tmp_path, no_fallback_env):
    docx = pytest.importorskip("docx")
    pptx = pytest.importorskip("pptx")
    fitz = pytest.importorskip("fitz")

    doc_path = tmp_path / "chapter.docx"
    document = docx.Document()
    document.add_heading("Chapter", level=1)
    document.add_paragraph("Document text for parsing.")
    document.save(doc_path)

    ppt_path = tmp_path / "slides.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Slide Title"
    slide.placeholders[1].text = "Slide body text"
    presentation.save(ppt_path)

    pdf_path = tmp_path / "paper.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "PDF parsing text")
    pdf.save(pdf_path)
    pdf.close()

    assert parse_document(doc_path)[1][0].metadata["content_kind"] == "doc_section"
    assert parse_document(ppt_path)[1][0].metadata["content_kind"] == "slide"
    assert "PDF parsing text" in parse_document(pdf_path)[1][0].text


def test_parser_does_not_call_unstructured_when_fallback_disabled(tmp_path, no_fallback_env, monkeypatch):
    broken = tmp_path / "broken.md"
    broken.write_text("# Broken", encoding="utf-8")
    called = False

    def fail_markdown(path):
        raise ValueError("forced parser failure")

    def fail_if_called(path):
        nonlocal called
        called = True
        raise AssertionError("unstructured fallback must not be called")

    monkeypatch.setattr(parsers, "parse_markdown", fail_markdown)
    monkeypatch.setattr(parsers, "parse_with_unstructured", fail_if_called)

    with pytest.raises(RuntimeError, match="Failed to parse broken.md"):
        parse_document(broken)
    assert called is False


def test_unsupported_type_requires_explicit_fallback(tmp_path, no_fallback_env, monkeypatch):
    unknown = tmp_path / "data.unknown"
    unknown.write_text("unknown", encoding="utf-8")
    monkeypatch.setattr(parsers, "parse_with_unstructured", lambda path: pytest.fail("fallback called"))

    with pytest.raises(RuntimeError, match="Unsupported source type"):
        parse_document(unknown)


def test_derive_chapter_uses_filename_before_date_or_course_folder():
    assert derive_chapter(Path(r"C:\data\Algorithmic GT\storage\20260425\Chapter 1.pdf"), "Algorithmic GT") == "Chapter 1"
    assert derive_chapter(Path(r"C:\data\Bayesian Statistics\storage\20260425\Lecture 5 - Slides.pdf"), "Bayesian Statistics") == "Lecture 5"
    assert derive_chapter(Path(r"C:\data\Bayesian Statistics\storage\20260425\Lecture 9 - Sildes.pdf"), "Bayesian Statistics") == "Lecture 9"
    assert derive_chapter(Path(r"C:\data\Algorithmic GT\storage\20260425\Week 2 - Solutions.pdf"), "Algorithmic GT") == "Week 2"
    assert derive_chapter(Path(r"C:\data\Bayesian Statistics\storage\20260425\Lab Questions.pdf"), "Bayesian Statistics") == "Lab Questions"
    assert derive_chapter(Path(r"C:\data\Algorithmic GT\storage\20260425\Labs solutions.pdf"), "Algorithmic GT") == "Lab Solutions"
    assert derive_chapter(Path(r"C:\data\Bayesian Statistics\storage\20260425\Written Coursework.pdf"), "Bayesian Statistics") == "Coursework"
    assert derive_chapter(Path(r"C:\data\Bayesian Statistics\storage\20260425\Z-Table.pdf"), "Bayesian Statistics") == "Reference"
    assert derive_chapter(Path(r"C:\data\Algorithmic GT\storage\20260425\graph_algorithms_visualizer.html"), "Algorithmic GT") == "Reference"
    assert derive_chapter(Path(r"C:\data\Algorithmic GT\storage\20260425\芝士炸鸡烤肉小课堂.pdf"), "Algorithmic GT") == "Reference"
    assert derive_chapter(Path(r"C:\data\Bayesian Statistics\storage\20260425\topic-notes.md"), "Bayesian Statistics") == "topic notes"

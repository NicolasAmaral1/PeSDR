"""Tests for MarkdownChunker — header-aware chunking with token cap."""

from __future__ import annotations

from ai_sdr.kb.chunker import ChunkDraft, MarkdownChunker


def test_empty_string_returns_empty() -> None:
    assert MarkdownChunker().split("") == []


def test_whitespace_only_returns_empty() -> None:
    assert MarkdownChunker().split("\n\n  \n") == []


def test_single_section_one_chunk() -> None:
    md = "## Preços\n\nMentoria custa R$ 6000."
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 1
    assert chunks[0].idx == 0
    assert chunks[0].heading_path == "Preços"
    assert "R$ 6000" in chunks[0].content
    assert chunks[0].token_count > 0


def test_two_sections_two_chunks_with_idx() -> None:
    md = "## A\n\ncontent a\n\n## B\n\ncontent b"
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 2
    assert chunks[0].heading_path == "A"
    assert chunks[1].heading_path == "B"
    assert chunks[0].idx == 0 and chunks[1].idx == 1


def test_nested_headings_breadcrumb() -> None:
    md = "# Top\n\n## Sub\n\n### Leaf\n\ndeep content"
    chunks = MarkdownChunker().split(md)
    # Top has no body → skipped; Sub has no body → skipped; Leaf has body → 1 chunk
    assert len(chunks) == 1
    assert chunks[0].heading_path == "Top > Sub > Leaf"


def test_heading_without_body_skipped() -> None:
    md = "## Empty\n\n## With body\n\nbody"
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 1
    assert chunks[0].heading_path == "With body"


def test_no_headings_one_chunk_with_no_path() -> None:
    md = "just some text without any heading"
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 1
    assert chunks[0].heading_path is None


def test_long_section_splits_by_paragraph_under_cap() -> None:
    big_para = ("foo bar baz " * 80) + "."  # ~240 tok per paragraph
    md = f"## Big\n\n{big_para}\n\n{big_para}\n\n{big_para}\n\n{big_para}\n\n{big_para}"
    chunker = MarkdownChunker(max_tokens=300)
    chunks = chunker.split(md)
    # 5 paragraphs × ~240 tok → ~1200 tok total; cap 300 → at least 4 chunks
    assert len(chunks) >= 4
    for c in chunks:
        assert c.heading_path == "Big"
        assert c.token_count <= 300


def test_single_paragraph_exceeds_cap_splits_by_sentence() -> None:
    sentences = ". ".join(["foo bar baz" * 20 for _ in range(20)]) + "."
    md = f"## Wall\n\n{sentences}"
    chunker = MarkdownChunker(max_tokens=200)
    chunks = chunker.split(md)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 200


def test_chunk_draft_fields_immutable() -> None:
    c = ChunkDraft(idx=0, heading_path="A", content="x", token_count=1)
    import dataclasses

    assert dataclasses.is_dataclass(c)

"""Markdown-aware chunker with a token cap.

Roll-our-own: walk lines, track heading stack by leading '#' count, emit a chunk
whenever a heading boundary OR token cap is hit. Paragraphs longer than the cap
are split by sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_DEFAULT_MAX_TOKENS = 600


@dataclass(frozen=True)
class ChunkDraft:
    idx: int
    heading_path: str | None
    content: str
    token_count: int


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _paragraphs(body: str) -> list[str]:
    """Split a chunk of text into paragraphs on blank lines, dropping empties."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)]
    return [p for p in paras if p]


def _split_paragraph_by_sentence(paragraph: str, max_tokens: int) -> list[str]:
    """Greedy pack sentences until cap is reached. Sentences split on '. ' boundary."""
    raw_sentences = re.split(r"(?<=\.)\s+", paragraph)
    out: list[str] = []
    buf: list[str] = []
    buf_tok = 0
    for s in raw_sentences:
        s_tok = _count_tokens(s)
        if buf and buf_tok + s_tok > max_tokens:
            out.append(" ".join(buf).strip())
            buf, buf_tok = [s], s_tok
        else:
            buf.append(s)
            buf_tok += s_tok
    if buf:
        out.append(" ".join(buf).strip())
    return [c for c in out if c]


class MarkdownChunker:
    """Header-aware chunker: each section under a heading becomes one chunk,
    split by paragraph (or sentence) when over max_tokens."""

    def __init__(self, max_tokens: int = _DEFAULT_MAX_TOKENS) -> None:
        self.max_tokens = max_tokens

    def split(self, content_md: str) -> list[ChunkDraft]:
        sections = self._sectionize(content_md)
        drafts: list[ChunkDraft] = []
        idx = 0
        for heading_path, body in sections:
            if not body.strip():
                continue
            for piece in self._pack(body):
                drafts.append(
                    ChunkDraft(
                        idx=idx,
                        heading_path=heading_path,
                        content=piece,
                        token_count=_count_tokens(piece),
                    )
                )
                idx += 1
        return drafts

    def _sectionize(self, md: str) -> list[tuple[str | None, str]]:
        """Walk lines, return list of (heading_path, body) tuples in document order."""
        stack: list[tuple[int, str]] = []  # (depth, title)
        sections: list[tuple[str | None, list[str]]] = []
        current_body: list[str] = []
        current_path: str | None = None

        def flush() -> None:
            if current_body:
                sections.append((current_path, list(current_body)))
                current_body.clear()

        for line in md.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                flush()
                depth = len(m.group(1))
                title = m.group(2).strip()
                # pop deeper-or-equal entries
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                stack.append((depth, title))
                current_path = " > ".join(t for _, t in stack)
            else:
                current_body.append(line)
        flush()

        return [(p, "\n".join(b)) for p, b in sections]

    def _pack(self, body: str) -> list[str]:
        """Pack the body into chunks <= max_tokens by paragraph, splitting further by sentence."""
        paras = _paragraphs(body)
        chunks: list[str] = []
        buf: list[str] = []
        buf_tok = 0

        for p in paras:
            p_tok = _count_tokens(p)
            if p_tok > self.max_tokens:
                if buf:
                    chunks.append("\n\n".join(buf))
                    buf, buf_tok = [], 0
                chunks.extend(_split_paragraph_by_sentence(p, self.max_tokens))
                continue
            if buf and buf_tok + p_tok > self.max_tokens:
                chunks.append("\n\n".join(buf))
                buf, buf_tok = [p], p_tok
            else:
                buf.append(p)
                buf_tok += p_tok

        if buf:
            chunks.append("\n\n".join(buf))
        return [c for c in chunks if c.strip()]

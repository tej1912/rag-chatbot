"""
Section-aware recursive chunking.

1. Split the document into SECTIONS at Markdown headers ('# Title' ... '###### Title').
   PDFs arrive pre-tagged '# Page N', so pages become sections.
2. Inside each section:
   - Q/A style text ('Q: ... A: ...') is split per pair, never mid-pair;
   - anything else is split recursively (paragraph -> line -> sentence -> word)
     to ~500 tokens with ~15% overlap.
3. Every chunk carries its section header as `header_context`; ingest prepends
   it to the text that gets embedded, so retrieval matches "Section: X + chunk".

Structure-based (not character-position based): a chunk always gets the header
of the section it was actually cut from.
"""
import re
from dataclasses import dataclass

CHARS_PER_TOKEN = 4
TARGET_TOKENS = 500
OVERLAP_RATIO = 0.15

TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = int(TARGET_CHARS * OVERLAP_RATIO)

HEADER_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
FAQ_MARKER_RE = re.compile(r"^\s*(?:Q:|Question:)", re.MULTILINE)
FAQ_SPLIT_RE = re.compile(r"(?=^\s*(?:Q:|Question:))", re.MULTILINE)


@dataclass
class ChunkResult:
    content: str
    header_context: str | None


def _split_recursive(text: str, target: int) -> list[str]:
    """Split on the largest separator first, recursing into oversized pieces."""
    separators = ["\n\n", "\n", ". ", " "]

    def split(chunk: str, seps: list[str]) -> list[str]:
        if len(chunk) <= target or not seps:
            return [chunk]
        sep = seps[0]
        out: list[str] = []
        buf = ""
        for piece in chunk.split(sep):
            candidate = (buf + sep + piece) if buf else piece
            if len(candidate) <= target:
                buf = candidate
            else:
                if buf:
                    out.append(buf)
                buf = piece
        if buf:
            out.append(buf)
        result: list[str] = []
        for piece in out:
            if len(piece) > target:
                result.extend(split(piece, seps[1:]))
            else:
                result.append(piece)
        return result

    return split(text, separators)


def _add_overlap(chunks: list[str], overlap: int) -> list[str]:
    if overlap <= 0 or len(chunks) < 2:
        return chunks
    return [chunks[0]] + [chunks[i - 1][-overlap:] + " " + chunks[i] for i in range(1, len(chunks))]


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """[(header or None, body)] in document order. Empty sections are dropped."""
    matches = list(HEADER_RE.finditer(text))
    if not matches:
        return [(None, text)]

    sections: list[tuple[str | None, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        if body:
            sections.append((m.group(1).strip(), body))
    return sections


def _chunk_body(body: str, faq_aware: bool) -> list[str]:
    if faq_aware and FAQ_MARKER_RE.search(body):
        out: list[str] = []
        for pair in FAQ_SPLIT_RE.split(body):
            pair = pair.strip()
            if not pair:
                continue
            out.extend([pair] if len(pair) <= TARGET_CHARS else _split_recursive(pair, TARGET_CHARS))
        return out
    return _add_overlap(_split_recursive(body, TARGET_CHARS), OVERLAP_CHARS)


def chunk_document(text: str, faq_aware: bool = True) -> list[ChunkResult]:
    text = text.strip()
    if not text:
        return []
    results: list[ChunkResult] = []
    for header, body in _split_sections(text):
        for piece in _chunk_body(body, faq_aware):
            piece = piece.strip()
            if piece:
                results.append(ChunkResult(content=piece, header_context=header))
    return results

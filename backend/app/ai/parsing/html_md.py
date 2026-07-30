"""HTML and Markdown — the two formats whose structure is headings, handled together because both
reduce to the same problem: turn a heading hierarchy plus body text into ordered
:class:`DocumentSection` objects with a correct ``heading_path``.

**HTML** is parsed with BeautifulSoup4 (``html.parser``, the stdlib backend — no ``lxml`` dependency
required even though it happens to be installed transitively via ``python-docx``, which keeps this
parser's own availability independent of that one). Script/style content is dropped before text
extraction; neither is ever meaningful to index and both routinely dwarf the actual document text.

**Markdown** is parsed by regex rather than a rendering library: the only thing this platform needs
from it is heading detection and paragraph text, not HTML rendering, so pulling in a markdown engine
for that would be dependency weight with no corresponding benefit. ``#`` through ``######`` ATX
headings are recognised; the far rarer Setext style (``===``/``---`` underlines) is not, matching
what every markdown linter treats as the modern convention.
"""

from __future__ import annotations

import re
from typing import Sequence

from app.ai.core.types import DocumentSection, RawDocument
from app.ai.parsing.base import DEFAULT_MAX_BYTES, BaseDocumentParser, sections_from_headings

HTML_NAME = "html"
HTML_MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})
HTML_EXTENSIONS = frozenset({".html", ".htm"})

MARKDOWN_NAME = "markdown"
MARKDOWN_MIME_TYPES = frozenset({"text/markdown", "text/x-markdown"})
MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# Triple-backtick or triple-tilde, the two CommonMark fence markers. Indentation before the fence
# is allowed by the spec; the language tag after it (if any) is not part of what makes it a fence.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class HtmlParser(BaseDocumentParser):
    """HTML, sectioned by its ``<h1>``–``<h6>`` hierarchy."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None) -> None:
        super().__init__(
            name=HTML_NAME, mime_types=HTML_MIME_TYPES, extensions=HTML_EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )

    def is_available(self) -> bool:
        try:
            import bs4  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        from bs4 import BeautifulSoup

        from app.ai.parsing.text import decode_best_effort

        soup = BeautifulSoup(decode_best_effort(document.content), "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        entries = list(_html_heading_entries(soup))
        sections = sections_from_headings(entries)
        text = "\n\n".join(s.text for s in sections)
        return text, sections


class MarkdownParser(BaseDocumentParser):
    """Markdown, sectioned by its ATX (``#``) heading hierarchy."""

    def __init__(self, *, max_bytes: int = DEFAULT_MAX_BYTES, recorder=None) -> None:
        super().__init__(
            name=MARKDOWN_NAME, mime_types=MARKDOWN_MIME_TYPES, extensions=MARKDOWN_EXTENSIONS,
            max_bytes=max_bytes, recorder=recorder,
        )

    def is_available(self) -> bool:
        return True

    def _parse(self, document: RawDocument) -> tuple[str, Sequence[DocumentSection]]:
        from app.ai.parsing.text import decode_best_effort

        raw = decode_best_effort(document.content)
        entries = list(_markdown_heading_entries(raw))
        sections = sections_from_headings(entries)
        text = "\n\n".join(s.text for s in sections)
        return text, sections


# ---------------------------------------------------------------------------
# format-specific (level, heading, body) extraction
# ---------------------------------------------------------------------------


def _html_heading_entries(soup) -> Sequence[tuple[int, str, str]]:  # noqa: ANN001
    """Walk the tree in document order, splitting at each heading tag."""
    heading_tags = {f"h{n}" for n in range(1, 7)}
    body = soup.body or soup
    blocks = list(body.descendants)

    entries: list[tuple[int, str, str]] = []
    current_level, current_heading, current_text = 0, "", []
    for node in blocks:
        name = getattr(node, "name", None)
        if name in heading_tags:
            entries.append((current_level, current_heading, " ".join(current_text).strip()))
            current_level = int(name[1])
            current_heading = node.get_text(" ", strip=True)
            current_text = []
        elif name is None and node.parent is not None and node.parent.name not in heading_tags:
            # A NavigableString whose own text is not the heading we just captured.
            stripped = str(node).strip()
            if stripped:
                current_text.append(stripped)
    entries.append((current_level, current_heading, " ".join(current_text).strip()))
    return [e for e in entries if e[1] or e[2]] or [(0, "", "")]


def _markdown_heading_entries(raw: str) -> Sequence[tuple[int, str, str]]:
    """``#`` lines are only real headings outside a fenced code block.

    A ``#``-prefixed shell comment or config line inside a ` ```bash ` block is body text, not
    structure — without tracking fence state, it would be parsed as a heading and fabricate a
    section boundary in the middle of a code sample.
    """
    lines = raw.split("\n")
    entries: list[tuple[int, str, str]] = []
    current_level, current_heading, current_lines = 0, "", []
    in_fence = False
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            current_lines.append(line)
            continue
        match = None if in_fence else _ATX_HEADING.match(line)
        if match:
            entries.append((current_level, current_heading, "\n".join(current_lines).strip()))
            current_level = len(match.group(1))
            current_heading = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    entries.append((current_level, current_heading, "\n".join(current_lines).strip()))
    return [e for e in entries if e[1] or e[2]] or [(0, "", "")]


__all__ = [
    "HTML_EXTENSIONS",
    "HTML_MIME_TYPES",
    "HTML_NAME",
    "MARKDOWN_EXTENSIONS",
    "MARKDOWN_MIME_TYPES",
    "MARKDOWN_NAME",
    "HtmlParser",
    "MarkdownParser",
]

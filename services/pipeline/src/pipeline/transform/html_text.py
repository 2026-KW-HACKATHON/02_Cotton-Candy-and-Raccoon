"""Extract readable notice text from an already decoded HTML body fragment."""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "center",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
)
HIDDEN_TAGS = frozenset(
    {
        "canvas",
        "del",
        "iframe",
        "noscript",
        "object",
        "s",
        "script",
        "strike",
        "style",
        "svg",
        "template",
    }
)
VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
FILE_SUFFIXES = (
    ".pdf",
    ".hwp",
    ".hwpx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".zip",
)


def _is_hidden(attributes: list[tuple[str, str | None]]) -> bool:
    values = dict(attributes)
    style = values.get("style") or ""
    return (
        "hidden" in values
        or (values.get("aria-hidden") or "").lower() == "true"
        or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I))
    )


def _is_file_link(attributes: list[tuple[str, str | None]]) -> bool:
    values = dict(attributes)
    if "download" in values:
        return True
    href = values.get("href") or ""
    try:
        parsed = urlsplit(href)
    except ValueError:
        return False
    query_keys = {key.lower() for key in parse_qs(parsed.query, keep_blank_values=True)}
    if query_keys.intersection({"q_filesn", "q_fileid"}):
        return True
    return unquote(parsed.path).lower().endswith(FILE_SUFFIXES)


class _NoticeHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tags: list[str] = []
        self.file_link_depth = 0
        self.previous_cell_was_header = False
        self.table_cell_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.hidden_tags:
            if tag not in VOID_TAGS:
                self.hidden_tags.append(tag)
            return
        if _is_hidden(attrs) or tag in HIDDEN_TAGS:
            if tag not in VOID_TAGS:
                self.hidden_tags.append(tag)
            return
        if self.file_link_depth:
            if tag == "a":
                self.file_link_depth += 1
            return
        if tag == "a" and _is_file_link(attrs):
            self.file_link_depth = 1
            self.parts.append(" ")
            return
        if tag == "br" or tag in BLOCK_TAGS:
            if tag == "tr":
                self.previous_cell_was_header = False
            self.parts.append(" " if self.table_cell_depth and tag != "tr" else "\n")
        elif tag == "th":
            self.previous_cell_was_header = False
            self.table_cell_depth += 1
            self.parts.append(" ")
        elif tag == "td":
            self.table_cell_depth += 1
            if self.previous_cell_was_header:
                while self.parts and self.parts[-1] == " ":
                    self.parts.pop()
                if self.parts:
                    self.parts[-1] = self.parts[-1].rstrip(" ")
            self.parts.append(": " if self.previous_cell_was_header else " ")
            self.previous_cell_was_header = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.hidden_tags:
            if tag in self.hidden_tags:
                last_match = len(self.hidden_tags) - 1 - self.hidden_tags[::-1].index(tag)
                del self.hidden_tags[last_match:]
            return
        if self.file_link_depth:
            if tag == "a":
                self.file_link_depth -= 1
                if not self.file_link_depth:
                    self.parts.append(" ")
            return
        if tag in BLOCK_TAGS:
            if tag == "tr":
                self.previous_cell_was_header = False
            self.parts.append(" " if self.table_cell_depth and tag != "tr" else "\n")
        elif tag == "th":
            self.previous_cell_was_header = True
            self.table_cell_depth = max(0, self.table_cell_depth - 1)
        elif tag == "td":
            self.table_cell_depth = max(0, self.table_cell_depth - 1)
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_tags and not self.file_link_depth:
            if not data.strip() or self.table_cell_depth:
                self.parts.append(re.sub(r"\s+", " ", data))
            else:
                normalized = data.replace("\r\n", "\n").replace("\r", "\n")
                self.parts.append(re.sub(r"[^\S\n]+", " ", normalized))


def html_to_notice_text(body_html: str | None) -> str:
    """Convert DESCRIPTION HTML to plain text without reading linked files.

    The collector's XML parser has already decoded the XML layer. HTMLParser
    decodes the HTML layer once; do not call html.unescape on this result.
    """
    if body_html is None:
        return ""
    parser = _NoticeHTMLParser()
    parser.feed(body_html)
    parser.close()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)

"""Convert rich text scripts to plain narration without altering their wording."""

from __future__ import annotations

import re
from pathlib import Path

from striprtf.striprtf import rtf_to_text

# Escaped braces are literal text; only unescaped braces change RTF group nesting.
_STRUCTURE = re.compile(rb"\\(?:[a-zA-Z]+-?\d* ?|'[0-9a-fA-F]{2}|[^a-zA-Z])|[{}]")


def read_rtf(path: Path) -> str:
    """Decode the document's declared code page and reject incomplete RTF before synthesis."""

    raw = path.read_bytes().removeprefix(b"\xef\xbb\xbf").lstrip()
    # Only inspect the opening controls: body text can contain similarly named commands.
    header = re.match(
        rb"\{\\rtf\d+(?=[\s\\{}])(?:\\[a-zA-Z]+-?\d* ?|[ \t\r\n])*", raw
    )
    if header is None:
        raise ValueError("Missing RTF header")

    depth = 0
    for token in _STRUCTURE.finditer(raw):
        value = token.group()
        if re.fullmatch(rb"\\bin(?:-?\d+)? ?", value):
            # The parser cannot safely distinguish binary payload bytes from RTF controls.
            raise ValueError("Embedded binary RTF content is not supported")
        hidden = re.fullmatch(rb"\\(?:v|deleted)(-?\d+)? ?", value)
        if hidden and (hidden[1] is None or int(hidden[1]) != 0):
            # striprtf otherwise includes hidden or tracked-deleted text in narration.
            raise ValueError("Hidden or deleted RTF text cannot be safely converted")
        if value == b"{":
            depth += 1
        elif value == b"}":
            depth -= 1
            if depth == 0 and raw[token.end() :].strip():
                raise ValueError("Unexpected data after the RTF document")
            if depth < 0:
                raise ValueError("Unbalanced RTF groups")
    if depth:
        raise ValueError("Incomplete RTF document")

    codepage = re.search(rb"\\ansicpg(\d+)", header.group())
    if codepage:
        encoding = f"cp{codepage[1].decode('ascii')}"
    else:
        # Older RTF files can specify a character set instead of an explicit code page.
        charset = re.search(rb"\\(mac|pca|pc)(?![a-zA-Z])", header.group())
        encoding = {b"mac": "mac_roman", b"pc": "cp437", b"pca": "cp850"}.get(
            charset[1] if charset else None, "cp1252"
        )
    text = rtf_to_text(raw.decode(encoding), encoding=encoding)
    # RTF represents non-BMP characters as UTF-16 surrogate pairs; Python needs real code points.
    return text.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le")

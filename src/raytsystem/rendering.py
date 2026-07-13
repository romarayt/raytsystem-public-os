from __future__ import annotations

import html
import re

_MARKDOWN_META = re.compile(r"([\\`*_{}\[\]()<>#+.!|~-])")


def escape_untrusted_markdown(value: str) -> str:
    """Render source text as inert inline Markdown, never as active markup or HTML."""

    single_line = " ".join(value.splitlines())
    escaped_html = html.escape(single_line, quote=True)
    return _MARKDOWN_META.sub(r"\\\1", escaped_html)

"""Render untrusted model output as Markdown with no executable HTML."""
import bleach
from markdown_it import MarkdownIt

_renderer = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
_tags = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "s",
         "ul", "ol", "li", "blockquote", "pre", "code", "a", "table", "thead", "tbody", "tr", "th", "td"}


def render_markdown(content: str) -> str:
    return bleach.clean(_renderer.render(content), tags=_tags,
                        attributes={"a": ["href", "title"], "ol": ["start"], "th": ["align"], "td": ["align"]},
                        protocols={"https", "http", "mailto"}, strip=True)

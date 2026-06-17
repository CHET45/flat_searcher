"""Small HTML tree helper built on the Python standard library."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Callable, Iterable


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["HtmlNode | str"] = field(default_factory=list)

    def attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    def has_class(self, class_name: str) -> bool:
        return class_name in self.attrs.get("class", "").split()

    def child_nodes(self, tag: str | None = None) -> list["HtmlNode"]:
        nodes = [child for child in self.children if isinstance(child, HtmlNode)]
        if tag is None:
            return nodes
        return [node for node in nodes if node.tag == tag]

    def iter_nodes(self) -> Iterable["HtmlNode"]:
        yield self
        for child in self.children:
            if isinstance(child, HtmlNode):
                yield from child.iter_nodes()

    def find_all(self, predicate: Callable[["HtmlNode"], bool]) -> list["HtmlNode"]:
        return [node for node in self.iter_nodes() if predicate(node)]

    def first(self, predicate: Callable[["HtmlNode"], bool]) -> "HtmlNode | None":
        for node in self.iter_nodes():
            if predicate(node):
                return node
        return None

    def text_content(self) -> str:
        parts: list[str] = []
        self._collect_text(parts)
        return _normalize_text("".join(parts))

    def _collect_text(self, parts: list[str]) -> None:
        if self.tag == "br":
            parts.append("\n")
            return
        for child in self.children:
            if isinstance(child, HtmlNode):
                child._collect_text(parts)
            else:
                parts.append(child)


class HtmlTreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag.lower(), {name.lower(): value or "" for name, value in attrs})
        self._stack[-1].children.append(node)
        if node.tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized_tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


def parse_html(html: str) -> HtmlNode:
    parser = HtmlTreeBuilder()
    parser.feed(html)
    parser.close()
    return parser.root


def _normalize_text(value: str) -> str:
    lines = []
    for line in unescape(value).replace("\xa0", " ").splitlines():
        normalized_line = " ".join(line.split())
        if normalized_line:
            lines.append(normalized_line)
    return "\n".join(lines)

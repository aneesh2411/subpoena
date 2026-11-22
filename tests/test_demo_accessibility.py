"""Check the published demo markup without a browser or network access."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "docs" / "demo"
PAGES = sorted(DEMO.rglob("*.html"))
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
CONTROLS = {"button", "input", "select", "textarea"}


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict[str, str]
    line: int
    parent: "Element | None"
    children: list["Element | str"] = field(default_factory=list)

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def hidden(self):
        return "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true"


class DemoHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def _add(self, tag, attrs):
        parent = self.stack[-1] if self.stack else None
        node = Element(tag, {key: value or "" for key, value in attrs}, self.getpos()[0], parent)
        self.elements.append(node)
        if parent is not None:
            parent.children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._add(tag, attrs)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].children.append(data)


def aria_name(node, ids):
    refs = node.attrs.get("aria-labelledby", "").split()
    return " ".join(content_name(ids[ref], ids) for ref in refs if ref in ids).strip() or (
        node.attrs.get("aria-label", "").strip()
    )


def content_name(node, ids, *, use_aria=False):
    if use_aria and (name := aria_name(node, ids)):
        return name
    if node.tag in {"img", "area"}:
        return node.attrs.get("alt", "").strip()
    if node.tag == "svg":
        return next(
            (
                content_name(child, ids)
                for child in node.children
                if isinstance(child, Element) and child.tag == "title"
            ),
            "",
        )
    if node.tag in CONTROLS - {"button"} or node.tag in {"script", "style", "template"}:
        return ""
    return " ".join(
        child if isinstance(child, str) else content_name(child, ids, use_aria=use_aria)
        for child in node.children
        if isinstance(child, str) or not child.hidden()
    ).strip()


def associated_labels(elements, controls, ids):
    implicit_targets = {}
    for control in controls:
        for ancestor in control.ancestors():
            if ancestor.tag == "label":
                implicit_targets.setdefault(ancestor, control)

    labels = defaultdict(list)
    for node in elements:
        if node.tag == "label":
            if "for" in node.attrs:
                target = ids.get(node.attrs["for"])
            else:
                target = implicit_targets.get(node)
            if target in controls:
                labels[target].append(node)
    return labels


def control_name(node, labels, ids):
    if name := aria_name(node, ids):
        return name
    for label in labels:
        if name := content_name(label, ids, use_aria=True):
            return name

    kind = node.attrs.get("type", "").lower()
    if node.tag == "button":
        return content_name(node, ids, use_aria=True)
    if node.tag == "input":
        if kind == "image":
            return node.attrs.get("alt", "").strip()
        if kind in {"button", "reset", "submit"}:
            return node.attrs.get("value", kind if kind != "button" else "").strip()
    return ""


def checklist(markup):
    document = DemoHTML()
    document.feed(markup)
    document.close()

    elements = document.elements
    ids = {node.attrs["id"]: node for node in elements if "id" in node.attrs}
    counts = Counter(node.attrs["id"] for node in elements if "id" in node.attrs)
    problems = [f"duplicate id {id_value!r}" for id_value, count in counts.items() if count > 1]

    page = [node for node in elements if all(a.tag != "template" for a in node.ancestors())]
    headings = [node for node in page if node.tag == "h1"]
    mains = [node for node in page if node.tag == "main" or node.attrs.get("role") == "main"]
    if len(headings) != 1:
        problems.append(f"expected exactly one h1, found {len(headings)}")
    elif not content_name(headings[0], ids, use_aria=True) or any(
        node.hidden() for node in (headings[0], *headings[0].ancestors())
    ):
        problems.append("h1 is empty or hidden")
    if len(mains) != 1:
        problems.append(f"expected exactly one main landmark, found {len(mains)}")
    elif mains[0].hidden() or any(a.hidden() for a in mains[0].ancestors()):
        problems.append("main landmark is hidden")

    controls = dict.fromkeys(
        node
        for node in elements
        if node.tag in CONTROLS
        and (node.tag != "input" or node.attrs.get("type", "").lower() != "hidden")
    )
    labels = associated_labels(elements, controls, ids)

    for node in elements:
        role = node.attrs.get("role")
        where = f"line {node.line} <{node.tag}>"
        if node.tag == "section" or role == "region":
            if not aria_name(node, ids):
                problems.append(f"{where} has no landmark label")
        if node in controls and not control_name(node, labels.get(node, ()), ids):
            problems.append(f"{where} has no associated label")
        if (node.tag in {"a", "area"} and "href" in node.attrs) or role == "link":
            name = content_name(node, ids, use_aria=True) or node.attrs.get("title", "").strip()
            if not name:
                problems.append(f"{where} has no discernible link text")
        if node.tag in {"img", "svg"} or role == "img":
            if node.hidden() or any(a.hidden() for a in node.ancestors()):
                continue
            if role in {"none", "presentation"} or (
                node.tag == "img" and node.attrs.get("alt") == ""
            ):
                continue
            name = aria_name(node, ids)
            if node.tag in {"img", "svg"}:
                name = name or content_name(node, ids)
            if not name:
                problems.append(f"{where} has no accessible image name")
    return problems


@pytest.mark.parametrize("page", PAGES, ids=[str(path.relative_to(DEMO)) for path in PAGES])
def test_demo_accessibility(page):
    problems = checklist(page.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_demo_accessibility_checklist_detects_missing_names_and_structure():
    invalid = """<h1>Demo</h1><h1>Extra heading</h1>
<section aria-labelledby="missing">
  <input id="query" placeholder="Search">
  <label for="other">Search</label><span id="other"></span>
  <a href="next.html"><span aria-hidden="true">Next</span></a>
  <img src="chart.png"><svg></svg>
  <div id="query"></div>
</section>"""
    assert checklist(invalid) == [
        "duplicate id 'query'",
        "expected exactly one h1, found 2",
        "expected exactly one main landmark, found 0",
        "line 2 <section> has no landmark label",
        "line 3 <input> has no associated label",
        "line 5 <a> has no discernible link text",
        "line 6 <img> has no accessible image name",
        "line 6 <svg> has no accessible image name",
    ]

    valid = """<main><h1>Demo</h1>
<section aria-labelledby="heading">
  <h2 id="heading">Search</h2>
  <label for="query">Query</label><input id="query">
  <label>Sort <select><option>Newest</option></select></label>
  <a href="next.html"><img src="next.png" alt="Next page"></a>
  <img src="divider.png" alt=""><svg aria-hidden="true"></svg>
  <svg role="img"><title>Search results trend</title></svg>
</section></main>"""
    assert checklist(valid) == []

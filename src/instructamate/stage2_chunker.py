"""Stage-2 chunker: verified Markdown units -> Parent/Child Chunk records.

The only input is the committed Markdown tree (ADR 0002 — never the PDFs).
Chunk identity follows ADR 0004: structural-path Chunk IDs plus a sha256
Content Hash of what the chunk currently says. Reference Patter isolation
(ADR 0001) is a structural guarantee of the emitted records.

Public seam: :func:`chunk_unit_markdown` — one verified Markdown string in,
``ChunkRecord`` list out. Fail-loud: unhandled structure raises
:class:`ChunkStructureError` rather than emitting silently-wrong records.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

__all__ = ["ChunkRecord", "ChunkStructureError", "chunk_unit_markdown"]

#: The full content_type taxonomy (CONTEXT.md — Content roles).
VALID_CONTENT_TYPES = frozenset(
    {
        "key_messages",
        "theory",
        "briefing",
        "exercise",
        "reference_patter",
        "common_problems",
        "airmanship",
        "aim",
        "competency",
        "self_check",
        "admin",
    }
)


class ChunkStructureError(ValueError):
    """The Markdown has structure the chunker has no verified rule for."""


@dataclass
class ChunkRecord:
    """One Parent or Child Chunk (CONTEXT.md vocabulary)."""

    id: str
    kind: str  # "parent" | "child"
    source: str
    unit: str
    unit_name: str
    revision: str
    content_type: str
    heading_path: list[str]
    pages: list[str]
    text: str
    content_hash: str
    embedding_text: str | None = None
    parent_id: str | None = None


_PAGE_MARKER = re.compile(r"^<!-- page: (?P<token>\S+) -->\s*$")
_CT_MARKER = re.compile(r"^<!-- content_type: (?P<value>\S+) -->\s*$")
_HEADING = re.compile(r"^(?P<hashes>#{1,6}) (?P<text>.+?)\s*$")


def _slugify(heading: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    if not slug:
        raise ChunkStructureError(f"heading {heading!r} produces an empty slug")
    return slug


def _split_frontmatter(md_text: str) -> tuple[dict[str, str], str]:
    if not md_text.startswith("---\n"):
        raise ChunkStructureError("unit Markdown does not start with frontmatter")
    try:
        _, fm, body = md_text.split("---\n", 2)
    except ValueError as exc:
        raise ChunkStructureError("unterminated frontmatter block") from exc
    meta: dict[str, str] = {}
    for line in fm.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta, body


@dataclass
class _Node:
    """One heading with its direct body — a Parent Chunk candidate."""

    level: int
    heading: str
    id_path: str
    heading_path: list[str]
    content_type: str | None
    body: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    child_slug_counts: dict[str, int] = field(default_factory=dict)

    def add_body_line(self, line: str, current_page: str | None) -> None:
        if not self.body and not line.strip():
            return  # leading blank before first content
        if current_page is not None and (not self.pages or self.pages[-1] != current_page):
            if line.strip():
                self.pages.append(current_page)
        self.body.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.body).strip("\n")


def chunk_unit_markdown(md_text: str) -> list[ChunkRecord]:
    """Chunk one verified unit Markdown string into ChunkRecords."""
    meta, body_text = _split_frontmatter(md_text)
    try:
        source = meta["source"]
        unit = meta["unit"]
        unit_name = meta["unit_name"]
        revision = meta["revision"]
    except KeyError as exc:
        raise ChunkStructureError(f"frontmatter is missing {exc.args[0]!r}") from exc

    unit_prefix = f"{source}:{unit}"
    nodes: list[_Node] = []  # document order — every heading gets a node
    stack: list[_Node] = []  # open headings, outermost first
    root_slug_counts: dict[str, int] = {}
    current_page: str | None = None

    for line in body_text.splitlines():
        page = _PAGE_MARKER.match(line)
        if page:
            current_page = page.group("token")
            continue

        ct = _CT_MARKER.match(line)
        if ct:
            value = ct.group("value")
            if value not in VALID_CONTENT_TYPES:
                raise ChunkStructureError(f"unknown content_type {value!r}")
            if not stack:
                raise ChunkStructureError("content_type marker outside any section")
            node = stack[-1]
            if node.body:
                raise ChunkStructureError(
                    f"content_type marker after body text under {node.heading!r}"
                )
            node.content_type = value
            continue

        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group("hashes"))
            text = heading.group("text")
            if level == 1:
                if stack:
                    raise ChunkStructureError(f"H1 {text!r} below the unit title")
                continue  # the unit title — not a chunk boundary
            while stack and stack[-1].level >= level:
                stack.pop()
            if level > 2 and not stack:
                raise ChunkStructureError(
                    f"sub-heading {text!r} with no enclosing ## section"
                )
            slug = _slugify(text)
            counts = stack[-1].child_slug_counts if stack else root_slug_counts
            counts[slug] = counts.get(slug, 0) + 1
            if counts[slug] > 1:
                slug = f"{slug}-{counts[slug]}"
            parent_path = stack[-1].id_path if stack else unit_prefix
            node = _Node(
                level=level,
                heading=text,
                id_path=f"{parent_path}:{slug}",
                heading_path=(stack[-1].heading_path if stack else []) + [text],
                content_type=stack[-1].content_type if stack else None,
            )
            nodes.append(node)
            stack.append(node)
            continue

        if not stack:
            if line.strip():
                raise ChunkStructureError(
                    f"body text before the first ## section: {line.strip()!r}"
                )
            continue
        stack[-1].add_body_line(line, current_page)

    records: list[ChunkRecord] = []
    for node in nodes:
        if node.content_type is None:
            raise ChunkStructureError(
                f"section {node.heading!r} has no content_type marker to apply or inherit"
            )
        text = node.text
        if not text:
            continue  # pure container heading — its leaves carry the content
        records.append(
            ChunkRecord(
                id=node.id_path,
                kind="parent",
                source=source,
                unit=unit,
                unit_name=unit_name,
                revision=revision,
                content_type=node.content_type,
                heading_path=node.heading_path,
                pages=list(node.pages),
                text=text,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return records

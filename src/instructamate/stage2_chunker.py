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

__all__ = ["ChunkRecord", "ChunkStructureError", "SyncPlan", "chunk_unit_markdown", "plan_sync"]

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


@dataclass
class SyncPlan:
    """Minimal Index work implied by freshly generated records (ADR 0004).

    Chunk IDs in record order for ``insert``/``update``; ``delete`` sorted
    (those IDs no longer exist in the records, so they have no record order).
    """

    insert: list[str] = field(default_factory=list)
    update: list[str] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)


def plan_sync(records: list[ChunkRecord], indexed_hashes: dict[str, str]) -> SyncPlan:
    """Reconcile fresh ChunkRecords against the Index's ``{id: hash}`` state."""
    plan = SyncPlan()
    for record in records:
        if record.id not in indexed_hashes:
            plan.insert.append(record.id)
        elif indexed_hashes[record.id] != record.content_hash:
            plan.update.append(record.id)
    generated = {record.id for record in records}
    plan.delete = sorted(chunk_id for chunk_id in indexed_hashes if chunk_id not in generated)
    return plan


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
    body: list[tuple[str, str | None]] = field(default_factory=list)  # (line, page)
    child_slug_counts: dict[str, int] = field(default_factory=dict)

    def add_body_line(self, line: str, current_page: str | None) -> None:
        if not self.body and not line.strip():
            return  # leading blank before first content
        self.body.append((line, current_page))

    @property
    def text(self) -> str:
        return "\n".join(line for line, _ in self.body).strip("\n")

    @property
    def pages(self) -> list[str]:
        return _pages_of(self.body)


def _pages_of(body: list[tuple[str, str | None]]) -> list[str]:
    pages: list[str] = []
    for line, page in body:
        if line.strip() and page is not None and (not pages or pages[-1] != page):
            pages.append(page)
    return pages


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
        common = dict(
            source=source,
            unit=unit,
            unit_name=unit_name,
            revision=revision,
            content_type=node.content_type,
            heading_path=node.heading_path,
        )
        records.append(
            ChunkRecord(
                id=node.id_path,
                kind="parent",
                pages=list(node.pages),
                text=text,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                **common,
            )
        )
        prefix = (
            f"{source.capitalize()} Guide, Unit {unit} — {unit_name} > "
            f"{' > '.join(node.heading_path)} [{node.content_type}]"
        )
        for ordinal, (child_text, child_pages) in enumerate(_child_blocks(node), start=1):
            embedding_text = f"{prefix}\n\n{child_text}"
            records.append(
                ChunkRecord(
                    id=f"{node.id_path}:c{ordinal}",
                    kind="child",
                    pages=child_pages,
                    text=child_text,
                    embedding_text=embedding_text,
                    content_hash=hashlib.sha256(embedding_text.encode("utf-8")).hexdigest(),
                    parent_id=node.id_path,
                    **common,
                )
            )
    return records


_BULLET_LINE = re.compile(r"^\s*(?:-|\d+\.)\s")
_TOP_BULLET = re.compile(r"^(?:-|\d+\.)\s")

#: Approximate embedding-size ceiling for one Child (whitespace tokens).
_CHILD_TOKEN_LIMIT = 500


def _child_blocks(node: _Node) -> list[tuple[str, list[str]]]:
    """Split a Parent's body into Child texts with the pages each spans.

    One prose paragraph, or one whole bullet list with its intro line (a
    preceding paragraph ending in ":"), per Child; nested sub-bullets stay
    with their list. Oversized lists split only at top-level bullets.
    """
    Block = tuple[str, list[tuple[str, str | None]]]  # (kind, lines)
    blocks: list[Block] = []
    current: list[tuple[str, str | None]] = []
    kind: str | None = None

    def flush() -> None:
        nonlocal current, kind
        if current:
            blocks.append((kind or "para", current))
        current, kind = [], None

    for line, page in node.body:
        if not line.strip():
            flush()
            continue
        line_kind = "list" if _BULLET_LINE.match(line) else "para"
        if kind is not None and line_kind != kind:
            flush()
        kind = line_kind
        current.append((line, page))
    flush()

    children: list[tuple[str, list[str]]] = []
    pending_intro: list[tuple[str, str | None]] | None = None
    for block_kind, lines in blocks:
        if block_kind == "para":
            if pending_intro is not None:
                children.append(_render_child(pending_intro))
            if lines[-1][0].rstrip().endswith(":"):
                pending_intro = lines  # may introduce a following bullet list
            else:
                pending_intro = None
                children.append(_render_child(lines))
            continue
        # A bullet list: attach the pending intro line, split if oversized.
        intro = pending_intro or []
        pending_intro = None
        for segment in _split_list(lines):
            children.append(_render_child(intro, segment))
            intro = []
    if pending_intro is not None:
        children.append(_render_child(pending_intro))
    return children


def _split_list(
    lines: list[tuple[str, str | None]],
) -> list[list[tuple[str, str | None]]]:
    """Split an oversized bullet list at top-level-bullet boundaries only."""
    if sum(len(line.split()) for line, _ in lines) <= _CHILD_TOKEN_LIMIT:
        return [lines]
    # A group = one top-level bullet with everything nested under it.
    groups: list[list[tuple[str, str | None]]] = []
    for line, page in lines:
        if _TOP_BULLET.match(line) or not groups:
            groups.append([])
        groups[-1].append((line, page))
    segments: list[list[tuple[str, str | None]]] = []
    segment: list[tuple[str, str | None]] = []
    size = 0
    for group in groups:
        tokens = sum(len(line.split()) for line, _ in group)
        if segment and size + tokens > _CHILD_TOKEN_LIMIT:
            segments.append(segment)
            segment, size = [], 0
        segment.extend(group)
        size += tokens
    if segment:
        segments.append(segment)
    return segments


def _render_child(
    *line_groups: list[tuple[str, str | None]],
) -> tuple[str, list[str]]:
    groups = [g for g in line_groups if g]
    text = "\n\n".join("\n".join(line for line, _ in group) for group in groups)
    pages = _pages_of([entry for group in groups for entry in group])
    return text, pages

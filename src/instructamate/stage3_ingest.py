"""Stage-3 ingest: Sync Plan → embed children → Atlas ``chunks`` collection.

Public seams (issue #34 / ADR 0004–0005):

- :func:`fetch_indexed_hashes` — Index ``{id: content_hash}`` for :func:`plan_sync`
- :func:`apply_sync` — embed insert/update children, upsert docs, delete by id
- :func:`chunk_record_to_document` — ChunkRecord → Atlas document shape
- :func:`ensure_vector_index` — code-ensure ``chunks_vector`` from committed JSON
- :func:`ensure_search_index` — code-ensure ``chunks_search`` (jargon-preserving analyzer)
- :class:`VoyageEmbedder` — explicit ``voyage-4-lite`` (``input_type=document``)

Embeddings are explicit Voyage calls, not Atlas Automated Embedding. Parents are
stored without vectors for expand payload.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Protocol, Sequence

from instructamate.stage2_chunker import ChunkRecord, SyncPlan

__all__ = [
    "COLLECTION_NAME",
    "DB_NAME",
    "EMBEDDING_DIMS",
    "EMBEDDING_MODEL",
    "Embedder",
    "SEARCH_INDEX_NAME",
    "SyncReport",
    "VECTOR_INDEX_NAME",
    "VoyageEmbedder",
    "apply_sync",
    "chunk_record_to_document",
    "chunks_collection",
    "ensure_search_index",
    "ensure_vector_index",
    "fetch_indexed_hashes",
    "ingest_corpus",
    "load_search_index_definition",
    "load_vector_index_definition",
]

DB_NAME = "instructamate"
COLLECTION_NAME = "chunks"
VECTOR_INDEX_NAME = "chunks_vector"
SEARCH_INDEX_NAME = "chunks_search"
EMBEDDING_MODEL = "voyage-4-lite"
EMBEDDING_DIMS = 1024


class Embedder(Protocol):
    """Thin port over Voyage (or a fake) — embed document texts only."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text (``input_type=document``)."""


@dataclass(frozen=True)
class SyncReport:
    """Counts of work performed by :func:`apply_sync`."""

    inserted: int
    updated: int
    deleted: int
    embedded: int


#: Voyage realtime embed endpoint accepts at most 128 inputs per request.
VOYAGE_EMBED_BATCH_SIZE = 128


class VoyageEmbedder:
    """Explicit Voyage embeddings for ingest (``input_type=document``)."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = EMBEDDING_MODEL,
        output_dimension: int = EMBEDDING_DIMS,
        batch_size: int = VOYAGE_EMBED_BATCH_SIZE,
        client: Any | None = None,
    ) -> None:
        if client is None:
            import voyageai

            client = voyageai.Client(api_key=api_key)
        self._client = client
        self.model = model
        self.output_dimension = output_dimension
        self.batch_size = batch_size

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            result = self._client.embed(
                batch,
                model=self.model,
                input_type="document",
                output_dimension=self.output_dimension,
            )
            embeddings.extend(result.embeddings)
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed a single retrieval query (``input_type=query``)."""
        result = self._client.embed(
            [text],
            model=self.model,
            input_type="query",
            output_dimension=self.output_dimension,
        )
        return list(result.embeddings[0])


def chunk_record_to_document(
    record: ChunkRecord,
    *,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    """Map a ChunkRecord to an ``instructamate.chunks`` document.

    Children require ``embedding``. Parents omit ``embedding``, ``parent_id``,
    and ``embedding_text``.
    """
    doc: dict[str, Any] = {
        "_id": record.id,
        "kind": record.kind,
        "source": record.source,
        "unit": record.unit,
        "unit_name": record.unit_name,
        "revision": record.revision,
        "content_type": record.content_type,
        "heading_path": list(record.heading_path),
        "pages": list(record.pages),
        "text": record.text,
        "content_hash": record.content_hash,
    }
    if record.kind == "child":
        if embedding is None:
            raise ValueError(f"child chunk {record.id!r} requires an embedding")
        if record.parent_id is None:
            raise ValueError(f"child chunk {record.id!r} requires parent_id")
        if record.embedding_text is None:
            raise ValueError(f"child chunk {record.id!r} requires embedding_text")
        doc["parent_id"] = record.parent_id
        doc["embedding"] = embedding
        doc["embedding_text"] = record.embedding_text
    return doc


def fetch_indexed_hashes(collection: Any) -> dict[str, str]:
    """Read ``{_id: content_hash}`` from the Index for Sync Plan reconciliation."""
    hashes: dict[str, str] = {}
    for doc in collection.find({}, {"_id": 1, "content_hash": 1}):
        hashes[doc["_id"]] = doc["content_hash"]
    return hashes


def apply_sync(
    records: list[ChunkRecord],
    plan: SyncPlan,
    *,
    collection: Any,
    embedder: Embedder,
) -> SyncReport:
    """Apply a Sync Plan: embed children on insert/update, upsert, delete."""
    by_id = {record.id: record for record in records}
    write_records = [by_id[chunk_id] for chunk_id in plan.insert + plan.update]

    children = [r for r in write_records if r.kind == "child"]
    embeddings: dict[str, list[float]] = {}
    if children:
        texts: list[str] = []
        for child in children:
            if child.embedding_text is None:
                raise ValueError(f"child chunk {child.id!r} requires embedding_text")
            texts.append(child.embedding_text)
        vectors = embedder.embed_documents(texts)
        if len(vectors) != len(children):
            raise RuntimeError(
                f"embedder returned {len(vectors)} vectors for {len(children)} children"
            )
        embeddings = {child.id: vector for child, vector in zip(children, vectors, strict=True)}

    for record in write_records:
        doc = chunk_record_to_document(record, embedding=embeddings.get(record.id))
        collection.replace_one({"_id": record.id}, doc, upsert=True)

    for chunk_id in plan.delete:
        collection.delete_one({"_id": chunk_id})

    return SyncReport(
        inserted=len(plan.insert),
        updated=len(plan.update),
        deleted=len(plan.delete),
        embedded=len(embeddings),
    )


def load_vector_index_definition() -> dict[str, Any]:
    """Load the committed ``chunks_vector`` definition body."""
    return _load_index_definition("chunks_vector.json")


def load_search_index_definition() -> dict[str, Any]:
    """Load the committed ``chunks_search`` definition body."""
    return _load_index_definition("chunks_search.json")


def ensure_vector_index(collection: Any) -> None:
    """Create ``chunks_vector`` if missing; fail loud if an existing index differs."""
    _ensure_index(
        collection,
        name=VECTOR_INDEX_NAME,
        index_type="vectorSearch",
        expected=load_vector_index_definition(),
        compatible=_vector_definitions_compatible,
        label="vector search index",
    )


def ensure_search_index(collection: Any) -> None:
    """Create ``chunks_search`` if missing; fail loud if an existing index differs."""
    _ensure_index(
        collection,
        name=SEARCH_INDEX_NAME,
        index_type="search",
        expected=load_search_index_definition(),
        compatible=_search_definitions_compatible,
        label="search index",
    )


def _load_index_definition(filename: str) -> dict[str, Any]:
    package = resources.files("instructamate") / "data" / filename
    payload = json.loads(package.read_text(encoding="utf-8"))
    return payload["definition"]


def _ensure_index(
    collection: Any,
    *,
    name: str,
    index_type: str,
    expected: dict[str, Any],
    compatible: Any,
    label: str,
) -> None:
    from pymongo.operations import SearchIndexModel

    _ensure_collection_exists(collection)
    existing = _find_search_index(collection, name)

    if existing is None:
        collection.create_search_index(
            model=SearchIndexModel(
                definition=expected,
                name=name,
                type=index_type,
            )
        )
        return

    if "latestDefinition" in existing:
        actual = existing["latestDefinition"]
    elif "definition" in existing:
        actual = existing["definition"]
    else:
        raise ValueError(
            f"{label} {name!r} exists but has no definition to compare"
        )
    if not compatible(expected, actual):
        raise ValueError(
            f"{label} {name!r} exists but is incompatible with the committed definition"
        )


def _find_search_index(collection: Any, name: str) -> dict[str, Any] | None:
    for index in collection.list_search_indexes():
        if index.get("name") == name:
            return index
    return None


def _ensure_collection_exists(collection: Any) -> None:
    """Create the collection if the handle is a real pymongo Collection."""
    from pymongo.collection import Collection

    if not isinstance(collection, Collection):
        return
    if collection.name not in collection.database.list_collection_names():
        collection.database.create_collection(collection.name)


def _vector_definitions_compatible(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare vector-index field lists ignoring Atlas-added bookkeeping keys."""
    return _normalize_vector_fields(expected.get("fields", [])) == _normalize_vector_fields(
        actual.get("fields", [])
    )


def _normalize_vector_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for field in fields:
        item = {"type": field.get("type"), "path": field.get("path")}
        if field.get("type") == "vector":
            item["numDimensions"] = field.get("numDimensions")
            item["similarity"] = field.get("similarity")
        normalized.append(item)
    return sorted(normalized, key=lambda f: (f.get("type") or "", f.get("path") or ""))


def _search_definitions_compatible(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Compare search-index analyzers + mappings (committed shape only)."""
    return _canonicalize(expected) == _canonicalize(
        {
            "analyzers": actual.get("analyzers", []),
            "mappings": actual.get("mappings", {}),
        }
    )


def _canonicalize(value: Any) -> Any:
    """Stable JSON-comparable form for nested index definitions."""
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def chunks_collection(uri: str) -> Any:
    """Return the ``instructamate.chunks`` collection for a MongoDB URI."""
    from pymongo import MongoClient

    client = MongoClient(uri)
    return client[DB_NAME][COLLECTION_NAME]


def ingest_corpus(
    md_root: str | Path,
    *,
    collection: Any,
    embedder: Embedder,
) -> SyncReport:
    """Chunk ``corpus/md``, reconcile via Sync Plan, embed children, write Atlas.

    Refuses when the Markdown tree yields no chunks: an empty corpus would make
    Sync Plan classify every indexed id as deleted and wipe the collection.
    """
    from instructamate.stage2_chunker import chunk_corpus, plan_sync

    ensure_vector_index(collection)
    ensure_search_index(collection)
    records = chunk_corpus(md_root)
    if not records:
        raise ValueError(
            f"refusing to sync: empty corpus under {Path(md_root)} "
            f"(no unit-*.md); would delete every indexed chunk"
        )
    plan = plan_sync(records, fetch_indexed_hashes(collection))
    return apply_sync(records, plan, collection=collection, embedder=embedder)

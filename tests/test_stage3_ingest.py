"""Stage-3 Atlas ingest seams (issue #34).

Unit tests use in-memory fakes only — no network. Live smoke lives in
``test_stage3_ingest_live.py`` and skips without credentials.
"""
from __future__ import annotations

from typing import Any, Sequence

import pytest

from instructamate.stage2_chunker import ChunkRecord, SyncPlan, plan_sync
from instructamate.stage3_ingest import (
    SyncReport,
    apply_sync,
    chunk_record_to_document,
    fetch_indexed_hashes,
)


class FakeCollection:
    """Minimal pymongo-shaped store for unit tests."""

    def __init__(self, docs: dict[str, dict[str, Any]] | None = None) -> None:
        self.docs: dict[str, dict[str, Any]] = {
            doc_id: dict(doc) for doc_id, doc in (docs or {}).items()
        }

    def find(self, filter: dict[str, Any] | None = None, projection: dict | None = None):
        for doc_id, doc in self.docs.items():
            out = {"_id": doc_id}
            if projection:
                for key, include in projection.items():
                    if include and key in doc:
                        out[key] = doc[key]
                    elif include and key == "_id":
                        out["_id"] = doc_id
            else:
                out.update(doc)
            yield out

    def replace_one(self, filter: dict[str, Any], replacement: dict[str, Any], upsert: bool = False):
        doc_id = filter["_id"]
        if doc_id not in self.docs and not upsert:
            raise KeyError(doc_id)
        self.docs[doc_id] = dict(replacement)

    def delete_one(self, filter: dict[str, Any]):
        self.docs.pop(filter["_id"], None)


class FakeEmbedder:
    """Deterministic embedder: vector = [hash ordinal, len(text), 0.0 …]."""

    def __init__(self, dims: int = 4) -> None:
        self.dims = dims
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for i, text in enumerate(texts):
            vec = [float(i + 1), float(len(text))] + [0.0] * (self.dims - 2)
            vectors.append(vec)
        return vectors


def _parent(**overrides) -> ChunkRecord:
    base = dict(
        id="trainer:5:key-messages",
        kind="parent",
        source="trainer",
        unit="5",
        unit_name="Primary Effects of Controls",
        revision="1.0",
        content_type="key_messages",
        heading_path=["KEY MESSAGES"],
        pages=["5-2"],
        text="The aircraft is a stable platform.",
        content_hash="parent-hash",
    )
    base.update(overrides)
    return ChunkRecord(**base)


def _child(**overrides) -> ChunkRecord:
    base = dict(
        id="trainer:5:key-messages:c1",
        kind="child",
        source="trainer",
        unit="5",
        unit_name="Primary Effects of Controls",
        revision="1.0",
        content_type="key_messages",
        heading_path=["KEY MESSAGES"],
        pages=["5-2"],
        text="The aircraft is a stable platform with three axes around the C of G.",
        content_hash="child-hash",
        embedding_text=(
            "Trainer Guide, Unit 5 — Primary Effects of Controls, revision 1.0 "
            "> KEY MESSAGES [key_messages]\n\n"
            "The aircraft is a stable platform with three axes around the C of G."
        ),
        parent_id="trainer:5:key-messages",
    )
    base.update(overrides)
    return ChunkRecord(**base)


def test_parent_document_has_no_embedding_or_parent_id():
    doc = chunk_record_to_document(_parent())

    assert doc["_id"] == "trainer:5:key-messages"
    assert doc["kind"] == "parent"
    assert doc["source"] == "trainer"
    assert doc["unit"] == "5"
    assert doc["content_type"] == "key_messages"
    assert doc["content_hash"] == "parent-hash"
    assert doc["text"] == "The aircraft is a stable platform."
    assert "embedding" not in doc
    assert "parent_id" not in doc
    assert "embedding_text" not in doc


def test_child_document_includes_vector_parent_id_and_embedding_text():
    vector = [0.1, 0.2, 0.3]
    doc = chunk_record_to_document(_child(), embedding=vector)

    assert doc["_id"] == "trainer:5:key-messages:c1"
    assert doc["kind"] == "child"
    assert doc["parent_id"] == "trainer:5:key-messages"
    assert doc["embedding"] == vector
    assert doc["embedding_text"].startswith("Trainer Guide, Unit 5")
    assert "stable platform" in doc["embedding_text"]


def test_child_document_requires_embedding():
    with pytest.raises(ValueError, match="embedding"):
        chunk_record_to_document(_child())


def test_fetch_indexed_hashes_returns_id_to_content_hash():
    coll = FakeCollection(
        {
            "trainer:5:aim": {"_id": "trainer:5:aim", "content_hash": "h1", "kind": "parent"},
            "trainer:5:key-messages:c1": {
                "_id": "trainer:5:key-messages:c1",
                "content_hash": "h2",
                "kind": "child",
            },
        }
    )

    assert fetch_indexed_hashes(coll) == {
        "trainer:5:aim": "h1",
        "trainer:5:key-messages:c1": "h2",
    }


def test_apply_sync_inserts_parents_without_vectors_and_embeds_children():
    parent = _parent()
    child = _child()
    records = [parent, child]
    plan = plan_sync(records, {})
    coll = FakeCollection()
    embedder = FakeEmbedder()

    report = apply_sync(records, plan, collection=coll, embedder=embedder)

    assert report == SyncReport(inserted=2, updated=0, deleted=0, embedded=1)
    assert embedder.calls == [[child.embedding_text]]
    assert "embedding" not in coll.docs[parent.id]
    assert coll.docs[child.id]["embedding"] == [1.0, float(len(child.embedding_text)), 0.0, 0.0]
    assert coll.docs[child.id]["parent_id"] == parent.id
    assert coll.docs[child.id]["embedding_text"] == child.embedding_text


def test_apply_sync_updates_changed_child_and_deletes_removed_ids():
    stale_parent = _parent(id="trainer:5:gone", content_hash="old")
    kept = _parent(content_hash="same")
    child = _child(content_hash="new-hash")
    coll = FakeCollection(
        {
            kept.id: chunk_record_to_document(kept),
            child.id: chunk_record_to_document(child, embedding=[9.0, 9.0, 9.0, 9.0])
            | {"content_hash": "old-hash"},
            stale_parent.id: chunk_record_to_document(stale_parent),
            "trainer:5:gone:c1": {
                "_id": "trainer:5:gone:c1",
                "kind": "child",
                "content_hash": "x",
                "embedding": [0.0],
            },
        }
    )
    # Child content_hash in index is old; record has new-hash → update.
    indexed = fetch_indexed_hashes(coll)
    records = [kept, child]
    plan = plan_sync(records, indexed)
    embedder = FakeEmbedder()

    report = apply_sync(records, plan, collection=coll, embedder=embedder)

    assert plan.update == [child.id]
    assert set(plan.delete) == {stale_parent.id, "trainer:5:gone:c1"}
    assert report == SyncReport(inserted=0, updated=1, deleted=2, embedded=1)
    assert child.id in coll.docs
    assert coll.docs[child.id]["content_hash"] == "new-hash"
    assert coll.docs[child.id]["embedding"] == [1.0, float(len(child.embedding_text)), 0.0, 0.0]
    assert stale_parent.id not in coll.docs
    assert "trainer:5:gone:c1" not in coll.docs


def test_apply_sync_fails_loud_when_embedder_raises():
    child = _child()
    plan = SyncPlan(insert=[child.id])

    class BoomEmbedder:
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("voyage down")

    with pytest.raises(RuntimeError, match="voyage down"):
        apply_sync([child], plan, collection=FakeCollection(), embedder=BoomEmbedder())


def test_ensure_vector_index_creates_when_missing():
    from instructamate.stage3_ingest import VECTOR_INDEX_NAME, ensure_vector_index, load_vector_index_definition

    class IndexAwareCollection(FakeCollection):
        def __init__(self) -> None:
            super().__init__()
            self.created: list[Any] = []
            self._indexes: list[dict[str, Any]] = []

        def list_search_indexes(self):
            return list(self._indexes)

        def create_search_index(self, model: Any = None, **kwargs):
            model = model if model is not None else kwargs.get("model")
            doc = model.document if hasattr(model, "document") else dict(model)
            self.created.append(doc)
            self._indexes.append(
                {
                    "name": doc["name"],
                    "type": doc.get("type", "vectorSearch"),
                    "latestDefinition": doc["definition"],
                }
            )
            return doc["name"]

    coll = IndexAwareCollection()
    definition = load_vector_index_definition()

    ensure_vector_index(coll)

    assert len(coll.created) == 1
    assert coll.created[0]["name"] == VECTOR_INDEX_NAME
    assert coll.created[0]["definition"] == definition
    assert definition["fields"][0]["numDimensions"] == 1024


def test_ensure_vector_index_is_noop_when_compatible():
    from instructamate.stage3_ingest import VECTOR_INDEX_NAME, ensure_vector_index, load_vector_index_definition

    definition = load_vector_index_definition()

    class IndexAwareCollection(FakeCollection):
        def list_search_indexes(self):
            return [{"name": VECTOR_INDEX_NAME, "type": "vectorSearch", "latestDefinition": definition}]

        def create_search_index(self, model: Any):
            raise AssertionError("must not recreate a compatible index")

    ensure_vector_index(IndexAwareCollection())


def test_ensure_vector_index_fails_loud_on_incompatible_existing():
    from instructamate.stage3_ingest import VECTOR_INDEX_NAME, ensure_vector_index

    class IndexAwareCollection(FakeCollection):
        def list_search_indexes(self):
            return [
                {
                    "name": VECTOR_INDEX_NAME,
                    "type": "vectorSearch",
                    "latestDefinition": {
                        "fields": [
                            {
                                "type": "vector",
                                "path": "embedding",
                                "numDimensions": 768,
                                "similarity": "cosine",
                            }
                        ]
                    },
                }
            ]

    with pytest.raises(ValueError, match="incompatible"):
        ensure_vector_index(IndexAwareCollection())


def test_voyage_embedder_batches_and_sets_document_input_type():
    from instructamate.stage3_ingest import VoyageEmbedder

    class FakeVoyageClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def embed(self, texts, **kwargs):
            self.calls.append({"texts": list(texts), **kwargs})

            class Result:
                embeddings = [[float(i), 0.0, 0.0, 0.0] for i in range(len(texts))]

            return Result()

    client = FakeVoyageClient()
    embedder = VoyageEmbedder(client=client, batch_size=2, output_dimension=4)
    vectors = embedder.embed_documents(["a", "b", "c"])

    assert len(vectors) == 3
    assert len(client.calls) == 2
    assert client.calls[0]["texts"] == ["a", "b"]
    assert client.calls[1]["texts"] == ["c"]
    assert client.calls[0]["input_type"] == "document"
    assert client.calls[0]["model"] == "voyage-4-lite"
    assert client.calls[0]["output_dimension"] == 4


def test_ingest_corpus_runs_full_sync_plan_over_md_tree(tmp_path):
    from instructamate.stage3_ingest import ingest_corpus

    md_root = tmp_path / "md" / "trainer"
    md_root.mkdir(parents=True)
    (md_root / "unit-05.md").write_text(
        """---
source: trainer
unit: "5"
unit_name: Primary Effects of Controls
revision: "1.0"
---

# Unit 5 — Primary Effects of Controls

<!-- page: 5-2 -->
## KEY MESSAGES
<!-- content_type: key_messages -->

- The aircraft is a stable platform with three axes around the C of G.
""",
        encoding="utf-8",
    )

    class IndexAwareCollection(FakeCollection):
        def list_search_indexes(self):
            return [
                {
                    "name": "chunks_vector",
                    "type": "vectorSearch",
                    "latestDefinition": {
                        "fields": [
                            {
                                "type": "vector",
                                "path": "embedding",
                                "numDimensions": 1024,
                                "similarity": "cosine",
                            },
                            {"type": "filter", "path": "source"},
                            {"type": "filter", "path": "unit"},
                            {"type": "filter", "path": "content_type"},
                            {"type": "filter", "path": "kind"},
                        ]
                    },
                }
            ]

    coll = IndexAwareCollection()
    report = ingest_corpus(tmp_path / "md", collection=coll, embedder=FakeEmbedder())

    assert report.inserted >= 2
    assert report.embedded >= 1
    assert "trainer:5:key-messages:c1" in coll.docs
    assert "embedding" in coll.docs["trainer:5:key-messages:c1"]
    assert "embedding" not in coll.docs["trainer:5:key-messages"]

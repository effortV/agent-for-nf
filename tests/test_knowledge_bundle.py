from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import (
    Chunk,
    Document,
    DocumentExtraction,
    DocumentStatus,
    ExtractedFact,
    ExtractionProfile,
    KnowledgeBase,
)
from app.services.knowledge_bundle import export_knowledge_bundle, import_knowledge_bundle


class _Graph:
    def __init__(self, *_args, **_kwargs):
        pass

    def replace_document(self, *_args):
        pass

    def upsert_profile_extraction(self, *_args):
        pass

    def close(self):
        pass


class _Vector:
    def __init__(self, *_args, **_kwargs):
        pass

    def replace_document(self, *_args):
        pass


def test_portable_bundle_reuses_parsed_chunks_and_versioned_extractions(monkeypatch, tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    source = KnowledgeBase(name="source")
    target = KnowledgeBase(name="target")
    db.add_all([source, target])
    db.flush()
    document = Document(
        knowledge_base_id=source.id,
        doi="10.1000/example",
        doi_normalized="10.1000/example",
        title="Machine learning nanofiltration",
        title_author_fingerprint="fingerprint-a",
        authors=[{"name": "A"}],
        status=DocumentStatus.indexed,
        metadata_json={"fulltext_mode": "fulltext"},
    )
    db.add(document)
    db.flush()
    chunk = Chunk(document_id=document.id, chunk_index=0, text="Flux was predicted by RF.", vector_id=f"{document.id}:0")
    fact = ExtractedFact(
        document_id=document.id,
        fact_type="performance",
        subject="membrane A",
        predicate="flux",
        object_text="20 LMH",
        source_sentence="Flux was predicted by RF.",
        confidence=0.9,
    )
    profile = ExtractionProfile(
        knowledge_base_id=source.id,
        name="ML extraction",
        version=1,
        research_question="ML for NF",
        schema_hash="a" * 64,
        schema_json={"fields": [{"name": "model"}]},
        prompt_text="extract",
    )
    db.add_all([chunk, fact, profile])
    db.flush()
    db.add(
        DocumentExtraction(
            document_id=document.id,
            profile_id=profile.id,
            status="completed",
            total_batches=1,
            completed_batches=1,
            facts_json=[{"field": "model", "chunk_id": chunk.id, "source_sentence": chunk.text}],
        )
    )
    db.commit()

    bundle = export_knowledge_bundle(db, source.id, Settings(storage_root=tmp_path / "objects", chroma_path=tmp_path / "chroma"))
    monkeypatch.setattr("app.services.knowledge_bundle.GraphStore", _Graph)
    monkeypatch.setattr("app.services.knowledge_bundle.VectorStore", _Vector)
    result = import_knowledge_bundle(
        db,
        target.id,
        bundle,
        Settings(storage_root=tmp_path / "objects2", chroma_path=tmp_path / "chroma2"),
    )

    assert result["documents_added"] == 1
    assert result["chunks"] == 1
    assert result["facts"] == 1
    assert result["profiles"] == 1
    assert result["extractions"] == 1
    target_document = db.scalar(select(Document).where(Document.knowledge_base_id == target.id))
    assert target_document.metadata_json["portable_bundle"] is True
    assert db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == target_document.id)) == 1
    imported = db.scalar(select(DocumentExtraction).where(DocumentExtraction.document_id == target_document.id))
    assert imported.facts_json[0]["chunk_id"] != chunk.id

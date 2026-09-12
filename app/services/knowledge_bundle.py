from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    Chunk,
    Document,
    DocumentExtraction,
    DocumentStatus,
    ExtractedFact,
    ExtractionProfile,
    KnowledgeBase,
    KnowledgeInsight,
)
from app.services.graph_store import GraphStore
from app.services.vector_store import VectorStore

BUNDLE_VERSION = 1
MAX_BUNDLE_BYTES = 512 * 1024 * 1024


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"不能序列化 {type(value).__name__}")


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, default=_json_default) + "\n").encode("utf-8")
        for row in rows
    )


def _rows(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    try:
        payload = archive.read(name)
    except KeyError:
        return []
    if len(payload) > MAX_BUNDLE_BYTES:
        raise ValueError(f"迁移包条目过大：{name}")
    output: list[dict[str, Any]] = []
    for line in payload.decode("utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                output.append(value)
    return output


def export_knowledge_bundle(db: Session, knowledge_base_id: str, settings: Settings | None = None) -> bytes:
    """Export reusable parsed knowledge, intentionally excluding credentials and source PDF bytes."""
    settings = settings or get_settings()
    knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
    if not knowledge_base:
        raise ValueError("知识库不存在")
    documents = list(
        db.scalars(
            select(Document)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.id.in_(select(Chunk.document_id)),
            )
            .order_by(Document.created_at)
        )
    )
    document_ids = [item.id for item in documents]
    chunks = list(db.scalars(select(Chunk).where(Chunk.document_id.in_(document_ids)))) if document_ids else []
    facts = (
        list(db.scalars(select(ExtractedFact).where(ExtractedFact.document_id.in_(document_ids))))
        if document_ids
        else []
    )
    profiles = list(
        db.scalars(select(ExtractionProfile).where(ExtractionProfile.knowledge_base_id == knowledge_base_id))
    )
    profile_ids = [item.id for item in profiles]
    extractions = (
        list(db.scalars(select(DocumentExtraction).where(DocumentExtraction.profile_id.in_(profile_ids))))
        if profile_ids
        else []
    )
    insights = list(
        db.scalars(select(KnowledgeInsight).where(KnowledgeInsight.knowledge_base_id == knowledge_base_id))
    )

    def pick(item: Any, names: tuple[str, ...]) -> dict[str, Any]:
        return {name: getattr(item, name) for name in names}

    files: dict[str, bytes] = {
        "documents.jsonl": _jsonl(
            [
                pick(
                    item,
                    (
                        "id",
                        "doi",
                        "doi_normalized",
                        "openalex_id",
                        "semantic_scholar_id",
                        "title",
                        "title_author_fingerprint",
                        "authors",
                        "abstract",
                        "publication_year",
                        "venue",
                        "landing_url",
                        "fulltext_url",
                        "fulltext_source",
                        "license",
                        "is_open_access",
                        "relevance_score",
                        "relevance_reasons",
                        "status",
                        "file_sha256",
                        "metadata_json",
                    ),
                )
                for item in documents
            ]
        ),
        "chunks.jsonl": _jsonl(
            [
                pick(
                    item,
                    (
                        "id",
                        "document_id",
                        "chunk_index",
                        "section",
                        "block_kind",
                        "source_label",
                        "page_start",
                        "page_end",
                        "text",
                        "token_count",
                    ),
                )
                for item in chunks
            ]
        ),
        "facts.jsonl": _jsonl(
            [
                pick(
                    item,
                    (
                        "id",
                        "document_id",
                        "fact_type",
                        "subject",
                        "predicate",
                        "object_text",
                        "value",
                        "unit",
                        "normalized_value",
                        "normalized_unit",
                        "conditions",
                        "source_sentence",
                        "page",
                        "table_id",
                        "confidence",
                        "review_status",
                        "raw_json",
                    ),
                )
                for item in facts
            ]
        ),
        "extraction_profiles.jsonl": _jsonl(
            [
                pick(
                    item,
                    (
                        "id",
                        "name",
                        "version",
                        "research_question",
                        "schema_hash",
                        "schema_json",
                        "prompt_text",
                        "model_name",
                        "status",
                    ),
                )
                for item in profiles
            ]
        ),
        "document_extractions.jsonl": _jsonl(
            [
                pick(
                    item,
                    (
                        "id",
                        "document_id",
                        "profile_id",
                        "status",
                        "total_batches",
                        "completed_batches",
                        "facts_json",
                        "error_message",
                        "model_name",
                    ),
                )
                for item in extractions
            ]
        ),
        "insights.jsonl": _jsonl(
            [
                pick(
                    item,
                    (
                        "id",
                        "question",
                        "insight_type",
                        "title",
                        "claim",
                        "rationale",
                        "evidence_document_ids",
                        "evidence_refs",
                        "assumptions",
                        "boundary_conditions",
                        "validation_plan",
                        "confidence",
                        "novelty_score",
                        "status",
                        "model_name",
                        "review_note",
                    ),
                )
                for item in insights
            ]
        ),
    }
    manifest = {
        "format": "nf-atlas-portable-knowledge",
        "version": BUNDLE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "knowledge_base": {"id": knowledge_base.id, "name": knowledge_base.name},
        "embedding_model": settings.embedding_model,
        "contains_source_files": False,
        "counts": {
            "documents": len(documents),
            "chunks": len(chunks),
            "facts": len(facts),
            "extraction_profiles": len(profiles),
            "document_extractions": len(extractions),
            "insights": len(insights),
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def import_knowledge_bundle(
    db: Session,
    knowledge_base_id: str,
    payload: bytes,
    settings: Settings | None = None,
    *,
    rebuild_indexes: bool = True,
) -> dict[str, Any]:
    """Import parsed artifacts and rebuild local indexes without LLM or PDF parsing calls."""
    settings = settings or get_settings()
    if not payload or len(payload) > MAX_BUNDLE_BYTES:
        raise ValueError("迁移包为空或超过 512 MB")
    knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
    if not knowledge_base:
        raise ValueError("知识库不存在")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        manifest = json.loads(archive.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("不是有效的 NF-Atlas 迁移包") from exc
    if manifest.get("format") != "nf-atlas-portable-knowledge" or manifest.get("version") != BUNDLE_VERSION:
        raise ValueError("迁移包格式或版本不受支持")

    document_rows = _rows(archive, "documents.jsonl")
    chunk_rows = _rows(archive, "chunks.jsonl")
    fact_rows = _rows(archive, "facts.jsonl")
    profile_rows = _rows(archive, "extraction_profiles.jsonl")
    extraction_rows = _rows(archive, "document_extractions.jsonl")
    insight_rows = _rows(archive, "insights.jsonl")
    document_map: dict[str, Document] = {}
    profile_map: dict[str, ExtractionProfile] = {}
    chunk_id_map: dict[str, str] = {}
    imported_documents: list[Document] = []
    counts = {"documents_added": 0, "documents_reused": 0, "chunks": 0, "facts": 0, "profiles": 0, "extractions": 0, "insights": 0}

    for raw in profile_rows:
        profile = db.scalar(
            select(ExtractionProfile).where(
                ExtractionProfile.knowledge_base_id == knowledge_base_id,
                ExtractionProfile.schema_hash == str(raw.get("schema_hash") or ""),
            )
        )
        if not profile:
            profile = ExtractionProfile(
                knowledge_base_id=knowledge_base_id,
                conversation_id=None,
                name=str(raw.get("name") or "导入抽取方案")[:240],
                version=int(raw.get("version") or 1),
                research_question=str(raw.get("research_question") or ""),
                schema_hash=str(raw.get("schema_hash") or "")[:64],
                schema_json=raw.get("schema_json") if isinstance(raw.get("schema_json"), dict) else {},
                prompt_text=str(raw.get("prompt_text") or ""),
                model_name=raw.get("model_name"),
                status=str(raw.get("status") or "active")[:40],
            )
            db.add(profile)
            db.flush()
            counts["profiles"] += 1
        profile_map[str(raw.get("id"))] = profile

    for raw in document_rows:
        doi = str(raw.get("doi_normalized") or "").strip() or None
        fingerprint = str(raw.get("title_author_fingerprint") or "").strip()
        conditions = []
        if doi:
            conditions.append(Document.doi_normalized == doi)
        if fingerprint:
            conditions.append(Document.title_author_fingerprint == fingerprint)
        if raw.get("openalex_id"):
            conditions.append(Document.openalex_id == raw["openalex_id"])
        if raw.get("file_sha256"):
            conditions.append(Document.file_sha256 == raw["file_sha256"])
        document = db.scalar(
            select(Document).where(Document.knowledge_base_id == knowledge_base_id, or_(*conditions))
        ) if conditions else None
        if document:
            counts["documents_reused"] += 1
        else:
            metadata = raw.get("metadata_json") if isinstance(raw.get("metadata_json"), dict) else {}
            metadata = {**metadata, "portable_bundle": True, "fulltext_mode": "parsed_bundle"}
            metadata.pop("import_job_id", None)
            metadata.pop("upgrade_pending", None)
            document = Document(
                knowledge_base_id=knowledge_base_id,
                doi=raw.get("doi"),
                doi_normalized=doi,
                openalex_id=raw.get("openalex_id"),
                semantic_scholar_id=raw.get("semantic_scholar_id"),
                title=str(raw.get("title") or "未命名文献"),
                title_author_fingerprint=fingerprint,
                authors=raw.get("authors") if isinstance(raw.get("authors"), list) else [],
                abstract=raw.get("abstract"),
                publication_year=raw.get("publication_year"),
                venue=raw.get("venue"),
                landing_url=raw.get("landing_url"),
                fulltext_url=raw.get("fulltext_url"),
                fulltext_source="portable-bundle",
                license=raw.get("license"),
                is_open_access=bool(raw.get("is_open_access")),
                relevance_score=float(raw.get("relevance_score") or 0),
                relevance_reasons=raw.get("relevance_reasons") if isinstance(raw.get("relevance_reasons"), list) else [],
                status=DocumentStatus.indexed,
                file_sha256=raw.get("file_sha256"),
                object_key=None,
                parsed_object_key=None,
                metadata_json=metadata,
            )
            db.add(document)
            db.flush()
            counts["documents_added"] += 1
        document_map[str(raw.get("id"))] = document

    chunks_by_document: dict[str, list[dict[str, Any]]] = {}
    for raw in chunk_rows:
        chunks_by_document.setdefault(str(raw.get("document_id")), []).append(raw)
    facts_by_document: dict[str, list[dict[str, Any]]] = {}
    for raw in fact_rows:
        facts_by_document.setdefault(str(raw.get("document_id")), []).append(raw)

    for old_id, document in document_map.items():
        current_chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)))
        if not current_chunks:
            for raw in sorted(chunks_by_document.get(old_id, []), key=lambda item: int(item.get("chunk_index") or 0)):
                chunk = Chunk(
                    document_id=document.id,
                    chunk_index=int(raw.get("chunk_index") or 0),
                    section=raw.get("section"),
                    block_kind=raw.get("block_kind"),
                    source_label=raw.get("source_label"),
                    page_start=raw.get("page_start"),
                    page_end=raw.get("page_end"),
                    text=str(raw.get("text") or ""),
                    token_count=raw.get("token_count"),
                )
                db.add(chunk)
                db.flush()
                chunk.vector_id = f"{document.id}:{chunk.chunk_index}"
                chunk_id_map[str(raw.get("id"))] = chunk.id
                current_chunks.append(chunk)
                counts["chunks"] += 1
            if current_chunks:
                imported_documents.append(document)
        else:
            indexed = {item.chunk_index: item for item in current_chunks}
            for raw in chunks_by_document.get(old_id, []):
                existing = indexed.get(int(raw.get("chunk_index") or 0))
                if existing:
                    chunk_id_map[str(raw.get("id"))] = existing.id

        existing_fact_count = len(list(db.scalars(select(ExtractedFact.id).where(ExtractedFact.document_id == document.id))))
        if not existing_fact_count:
            for raw in facts_by_document.get(old_id, []):
                db.add(
                    ExtractedFact(
                        document_id=document.id,
                        fact_type=str(raw.get("fact_type") or "relation")[:80],
                        subject=str(raw.get("subject") or document.title)[:500],
                        predicate=str(raw.get("predicate") or "reports")[:200],
                        object_text=raw.get("object_text"),
                        value=raw.get("value"),
                        unit=raw.get("unit"),
                        normalized_value=raw.get("normalized_value"),
                        normalized_unit=raw.get("normalized_unit"),
                        conditions=raw.get("conditions") if isinstance(raw.get("conditions"), dict) else {},
                        source_sentence=str(raw.get("source_sentence") or ""),
                        page=raw.get("page"),
                        table_id=raw.get("table_id"),
                        confidence=float(raw.get("confidence") or 0),
                        review_status=str(raw.get("review_status") or "pending")[:30],
                        raw_json=raw.get("raw_json") if isinstance(raw.get("raw_json"), dict) else {},
                    )
                )
                counts["facts"] += 1
    db.commit()

    for raw in extraction_rows:
        document = document_map.get(str(raw.get("document_id")))
        profile = profile_map.get(str(raw.get("profile_id")))
        if not document or not profile:
            continue
        existing = db.scalar(
            select(DocumentExtraction).where(
                DocumentExtraction.document_id == document.id,
                DocumentExtraction.profile_id == profile.id,
            )
        )
        if existing and existing.status == "completed":
            continue
        remapped_facts = []
        for fact in raw.get("facts_json") if isinstance(raw.get("facts_json"), list) else []:
            if not isinstance(fact, dict):
                continue
            fact = dict(fact)
            if fact.get("chunk_id") in chunk_id_map:
                fact["chunk_id"] = chunk_id_map[fact["chunk_id"]]
            remapped_facts.append(fact)
        artifact = existing or DocumentExtraction(document_id=document.id, profile_id=profile.id)
        artifact.status = str(raw.get("status") or "completed")[:40]
        artifact.total_batches = int(raw.get("total_batches") or 0)
        artifact.completed_batches = int(raw.get("completed_batches") or 0)
        artifact.facts_json = remapped_facts
        artifact.error_message = raw.get("error_message")
        artifact.model_name = raw.get("model_name")
        if artifact.status == "completed":
            artifact.completed_at = datetime.now(UTC)
        if not existing:
            db.add(artifact)
        counts["extractions"] += 1

    for raw in insight_rows:
        old_evidence = raw.get("evidence_document_ids") if isinstance(raw.get("evidence_document_ids"), list) else []
        mapped_evidence = [document_map[item].id for item in old_evidence if item in document_map]
        duplicate = db.scalar(
            select(KnowledgeInsight).where(
                KnowledgeInsight.knowledge_base_id == knowledge_base_id,
                KnowledgeInsight.title == str(raw.get("title") or ""),
                KnowledgeInsight.claim == str(raw.get("claim") or ""),
            )
        )
        if duplicate:
            continue
        db.add(
            KnowledgeInsight(
                knowledge_base_id=knowledge_base_id,
                conversation_id=None,
                question=str(raw.get("question") or ""),
                insight_type=str(raw.get("insight_type") or "pattern")[:40],
                title=str(raw.get("title") or "导入知识发现")[:500],
                claim=str(raw.get("claim") or ""),
                rationale=str(raw.get("rationale") or ""),
                evidence_document_ids=mapped_evidence,
                evidence_refs=raw.get("evidence_refs") if isinstance(raw.get("evidence_refs"), list) else [],
                assumptions=raw.get("assumptions") if isinstance(raw.get("assumptions"), list) else [],
                boundary_conditions=raw.get("boundary_conditions") if isinstance(raw.get("boundary_conditions"), list) else [],
                validation_plan=raw.get("validation_plan") if isinstance(raw.get("validation_plan"), dict) else {},
                confidence=float(raw.get("confidence") or 0),
                novelty_score=float(raw.get("novelty_score") or 0),
                status=str(raw.get("status") or "ai_hypothesis")[:40],
                model_name=raw.get("model_name"),
                review_note=raw.get("review_note"),
            )
        )
        counts["insights"] += 1
    knowledge_base.index_version += 1
    db.commit()

    index_document_ids = list(dict.fromkeys(item.id for item in document_map.values()))
    if not rebuild_indexes:
        return {
            **counts,
            "index_version": knowledge_base.index_version,
            "warnings": [],
            "index_document_ids": index_document_ids,
        }

    graph = GraphStore(settings)
    vector_store = VectorStore(settings)
    warnings: list[str] = []
    try:
        for document in document_map.values():
            chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)))
            facts = list(db.scalars(select(ExtractedFact).where(ExtractedFact.document_id == document.id)))
            try:
                graph.replace_document(document, facts)
                if document in imported_documents:
                    vector_store.replace_document(document, chunks)
                artifacts = list(
                    db.scalars(select(DocumentExtraction).where(DocumentExtraction.document_id == document.id))
                )
                for artifact in artifacts:
                    profile = db.get(ExtractionProfile, artifact.profile_id)
                    if profile:
                        graph.upsert_profile_extraction(document, profile, artifact)
            except Exception as exc:
                warnings.append(f"{document.title[:100]}：索引重建失败 {type(exc).__name__}")
    finally:
        graph.close()
    return {
        **counts,
        "index_version": knowledge_base.index_version,
        "warnings": warnings[:50],
        "index_document_ids": index_document_ids,
    }

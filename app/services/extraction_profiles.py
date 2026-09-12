from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Chunk, Conversation, Document, DocumentExtraction, ExtractionProfile
from app.services.llm import DeepSeekClient

BASE_FIELDS = [
    {"name": "membrane_material", "description": "膜材料、单体、支撑体和添加剂"},
    {"name": "membrane_batch", "description": "膜名称、样品或批次"},
    {"name": "preparation", "description": "制备步骤与工艺参数"},
    {"name": "separation_system", "description": "溶质、溶剂和进料组成"},
    {"name": "performance", "description": "通量、渗透率、截留率和选择性"},
    {"name": "structure", "description": "孔径、电荷、粗糙度、厚度和化学结构"},
    {"name": "mechanism", "description": "原文明确陈述的机理"},
]


def _fallback_schema(question: str) -> dict[str, Any]:
    fields = list(BASE_FIELDS)
    folded = question.casefold()
    if any(term in folded for term in ("机器学习", "machine learning", "deep learning", "神经网络")):
        fields.extend(
            [
                {"name": "dataset", "description": "数据来源、样本量和数据清洗"},
                {"name": "features", "description": "输入特征和特征工程"},
                {"name": "model", "description": "算法、超参数和基线"},
                {"name": "validation", "description": "数据划分、交叉验证和外部验证"},
                {"name": "model_metrics", "description": "R2、RMSE、MAE、准确率等指标"},
                {"name": "interpretability", "description": "特征重要性、SHAP和适用域"},
            ]
        )
    if any(term in folded for term in ("li/mg", "锂镁", "盐湖提锂", "lithium")):
        fields.extend(
            [
                {"name": "ion_composition", "description": "Li、Mg及共存离子浓度"},
                {"name": "li_mg_selectivity", "description": "Li/Mg选择性、分离因子和回收率"},
            ]
        )
    return {
        "objective": question,
        "fields": fields,
        "conditions": ["pressure", "temperature", "feed_concentration", "pH", "solute", "solvent"],
        "evidence": ["source_sentence", "page", "section", "table_id", "chunk_id"],
    }


def _canonical_schema(value: dict[str, Any], question: str) -> dict[str, Any]:
    fields = value.get("fields") if isinstance(value.get("fields"), list) else []
    clean_fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in fields:
        if isinstance(item, str):
            name, description = item, item
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("field") or "")
            description = str(item.get("description") or item.get("meaning") or name)
        else:
            continue
        name = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", "_", name.strip()).strip("_")[:80]
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        clean_fields.append({"name": name, "description": description.strip()[:500]})
    if not clean_fields:
        clean_fields = _fallback_schema(question)["fields"]
    conditions = value.get("conditions") if isinstance(value.get("conditions"), list) else []
    return {
        "objective": str(value.get("objective") or question).strip()[:2000],
        "fields": clean_fields[:40],
        "conditions": list(dict.fromkeys(str(item).strip()[:80] for item in conditions if str(item).strip()))[:30]
        or _fallback_schema(question)["conditions"],
        "evidence": ["source_sentence", "page", "section", "table_id", "chunk_id"],
    }


def _schema_hash(schema: dict[str, Any]) -> str:
    identity = {
        "fields": sorted(str(item["name"]).casefold() for item in schema["fields"]),
        "conditions": sorted(str(item).casefold() for item in schema["conditions"]),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class ExtractionProfileService:
    def __init__(self, llm: DeepSeekClient | None = None):
        self.llm = llm or DeepSeekClient(enable_thinking=False)

    async def ensure_for_conversation(
        self,
        db: Session,
        conversation: Conversation,
        question: str,
    ) -> tuple[ExtractionProfile, bool]:
        schema = _fallback_schema(question)
        name = question.strip()[:80] or "纳滤通用抽取"
        if self.llm.configured:
            try:
                response = await self.llm.json_chat(
                    system=(
                        "你是科研知识工程师。根据纳滤研究问题生成可复用的文献结构化抽取方案。"
                        "返回 name、objective、fields、conditions；fields 每项包含 name 和 description。"
                        "字段应覆盖回答问题所需的材料、制备、实验条件、性能、机理或机器学习信息，最多40项。"
                        "每个事实之后都必须能绑定原文句子、章节、页码、表格和切片编号。"
                    ),
                    user=json.dumps(
                        {
                            "question": question,
                            "rolling_summary": conversation.rolling_summary,
                            "current_research_task": conversation.current_task,
                        },
                        ensure_ascii=False,
                    ),
                    max_tokens=2500,
                    enable_thinking=False,
                )
                if isinstance(response, dict):
                    schema = _canonical_schema(response, question)
                    name = str(response.get("name") or name).strip()[:240]
            except Exception:
                schema = _fallback_schema(question)
        schema = _canonical_schema(schema, question)
        digest = _schema_hash(schema)
        existing = db.scalar(
            select(ExtractionProfile).where(
                ExtractionProfile.knowledge_base_id == conversation.knowledge_base_id,
                ExtractionProfile.schema_hash == digest,
            )
        )
        if existing:
            return existing, False
        version = (
            db.scalar(
                select(func.max(ExtractionProfile.version)).where(
                    ExtractionProfile.knowledge_base_id == conversation.knowledge_base_id
                )
            )
            or 0
        ) + 1
        prompt = (
            "仅抽取论文原文明确陈述并属于本研究方案的事实。每项输出 field、subject、predicate、"
            "object_text、value、unit、conditions、source_sentence、page、section、table_id、chunk_id、confidence。"
            "不得推测，不得把综述引用的他人数据当作本论文实验结果。"
        )
        profile = ExtractionProfile(
            knowledge_base_id=conversation.knowledge_base_id,
            conversation_id=conversation.id,
            name=name,
            version=version,
            research_question=question,
            schema_hash=digest,
            schema_json=schema,
            prompt_text=prompt,
            model_name=self.llm.model_name,
        )
        db.add(profile)
        db.flush()
        return profile, True

    async def extract_document(
        self,
        db: Session,
        document: Document,
        profile: ExtractionProfile,
        *,
        batch_size: int = 6,
    ) -> DocumentExtraction:
        artifact = db.scalar(
            select(DocumentExtraction).where(
                DocumentExtraction.document_id == document.id,
                DocumentExtraction.profile_id == profile.id,
            )
        )
        if artifact and artifact.status == "completed":
            return artifact
        chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)))
        total_batches = math.ceil(len(chunks) / batch_size) if chunks else 0
        if not artifact:
            artifact = DocumentExtraction(
                document_id=document.id,
                profile_id=profile.id,
                status="queued",
                total_batches=total_batches,
                model_name=self.llm.model_name,
            )
            db.add(artifact)
            db.flush()
        artifact.total_batches = total_batches
        if not self.llm.configured or not chunks:
            artifact.status = "failed"
            artifact.error_message = "DeepSeek API 未配置" if not self.llm.configured else "文献没有可复用切片"
            artifact.completed_at = datetime.now(UTC)
            db.commit()
            return artifact
        artifact.status = "extracting"
        artifact.error_message = None
        db.commit()
        facts = list(artifact.facts_json or [])
        try:
            for batch_index in range(artifact.completed_batches, total_batches):
                batch = chunks[batch_index * batch_size : (batch_index + 1) * batch_size]
                by_id = {chunk.id: chunk for chunk in batch}
                payload = [
                    {
                        "chunk_id": chunk.id,
                        "section": chunk.section,
                        "page": chunk.page_start,
                        "block_kind": chunk.block_kind,
                        "table_id": chunk.source_label if chunk.block_kind == "table" else None,
                        "text": chunk.text,
                    }
                    for chunk in batch
                ]
                response = await self.llm.json_chat(
                    system=profile.prompt_text,
                    user=json.dumps(
                        {
                            "document": {"title": document.title, "doi": document.doi_normalized},
                            "extraction_schema": profile.schema_json,
                            "chunks": payload,
                        },
                        ensure_ascii=False,
                    ),
                    max_tokens=6000,
                    enable_thinking=False,
                )
                raw_facts = response.get("facts", []) if isinstance(response, dict) else []
                for raw in raw_facts if isinstance(raw_facts, list) else []:
                    if not isinstance(raw, dict):
                        continue
                    chunk = by_id.get(str(raw.get("chunk_id") or ""))
                    sentence = str(raw.get("source_sentence") or "").strip()
                    if not chunk or not sentence or re.sub(r"\s+", " ", sentence) not in re.sub(r"\s+", " ", chunk.text):
                        continue
                    fact = {
                        "field": str(raw.get("field") or "relation")[:80],
                        "subject": str(raw.get("subject") or document.title)[:500],
                        "predicate": str(raw.get("predicate") or raw.get("field") or "reports")[:200],
                        "object_text": str(raw.get("object_text") or "")[:4000] or None,
                        "value": raw.get("value"),
                        "unit": str(raw.get("unit") or "")[:80] or None,
                        "conditions": raw.get("conditions") if isinstance(raw.get("conditions"), dict) else {},
                        "source_sentence": sentence[:4000],
                        "page": chunk.page_start,
                        "section": chunk.section,
                        "table_id": chunk.source_label if chunk.block_kind == "table" else raw.get("table_id"),
                        "chunk_id": chunk.id,
                        "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.0))),
                    }
                    facts.append(fact)
                artifact.facts_json = facts
                artifact.completed_batches = batch_index + 1
                db.commit()
            artifact.status = "completed"
            artifact.completed_at = datetime.now(UTC)
            db.commit()
            return artifact
        except Exception as exc:
            db.rollback()
            artifact = db.get(DocumentExtraction, artifact.id)
            if artifact:
                artifact.status = "failed"
                artifact.error_message = f"{type(exc).__name__}: {str(exc)[:1800]}"
                db.commit()
            raise

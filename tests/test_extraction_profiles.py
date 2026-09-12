import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import Conversation, KnowledgeBase
from app.services.extraction_profiles import ExtractionProfileService
from app.services.llm import DeepSeekClient


def test_conversation_generates_ml_scheme_and_reuses_same_version() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    kb = KnowledgeBase(name="test")
    db.add(kb)
    db.flush()
    conversation = Conversation(knowledge_base_id=kb.id, current_task="机器学习预测纳滤膜性能")
    db.add(conversation)
    db.commit()
    service = ExtractionProfileService(DeepSeekClient(Settings(siliconflow_api_key=None)))

    first, created = asyncio.run(
        service.ensure_for_conversation(db, conversation, "机器学习如何预测纳滤膜通量和截留率")
    )
    db.commit()
    second, created_again = asyncio.run(
        service.ensure_for_conversation(db, conversation, "机器学习如何预测纳滤膜通量和截留率")
    )

    fields = {item["name"] for item in first.schema_json["fields"]}
    assert {"dataset", "features", "model", "validation", "model_metrics"}.issubset(fields)
    assert created is True
    assert created_again is False
    assert second.id == first.id

import json

from app.models import TrainingTrace
from app.services.training import trace_to_dataset_record, traces_to_jsonl, training_quality


def _trace() -> TrainingTrace:
    evidence = [
        {
            "document_id": f"doc-{index % 3}",
            "doi": f"10.1000/{index}",
            "page": index + 1,
            "quote": f"evidence {index}",
            "evidence_mode": "fulltext" if index < 5 else "metadata-only",
        }
        for index in range(6)
    ]
    return TrainingTrace(
        id="trace-1",
        conversation_id="conversation-1",
        message_id="message-1",
        instruction="只依据知识库证据回答",
        input_text="比较两种纳滤膜",
        output_text="原回答",
        retrieval_evidence=evidence,
        tool_trace=[{"tool": "neo4j"}, {"tool": "chroma"}],
        human_revision="人工修订回答",
        rating=5,
        approved_for_training=True,
    )


def test_training_export_prefers_human_revision_and_keeps_audit_metadata() -> None:
    trace = _trace()
    score, label = training_quality(trace)
    assert score >= 0.75
    assert label == "高质量"

    record = trace_to_dataset_record(trace)
    assert record["output"] == "人工修订回答"
    assert record["metadata"]["approved_for_training"] is True
    assert len(record["metadata"]["retrieval_evidence"]) == 6

    line = traces_to_jsonl([trace]).decode("utf-8").strip()
    assert json.loads(line)["input"] == "比较两种纳滤膜"


def test_unreviewed_trace_is_not_mislabeled_high_quality() -> None:
    trace = _trace()
    trace.retrieval_evidence = []
    trace.tool_trace = []
    trace.human_revision = None
    trace.rating = None
    score, label = training_quality(trace)
    assert score == 0
    assert label == "待审核"

from app.services.knowledge_discovery import KnowledgeDiscoveryEngine


def _evidence(mode: str = "fulltext") -> list[dict]:
    return [
        {
            "document_id": f"doc-{index}",
            "title": f"Paper {index}",
            "doi": f"10.1000/{index}",
            "quote": f"Evidence {index}",
            "page": index,
            "evidence_mode": mode,
        }
        for index in range(1, 4)
    ]


def test_hypothesis_requires_multiple_evidence_and_validation_plan() -> None:
    engine = object.__new__(KnowledgeDiscoveryEngine)
    raw = {
        "type": "hypothesis",
        "title": "Cross-paper mechanism",
        "claim": "A coupled morphology-charge mechanism may govern selectivity.",
        "rationale": "Two independent reports show complementary trends.",
        "supporting_evidence_ids": [1, 2],
        "contradicting_evidence_ids": [],
        "assumptions": ["Comparable feed chemistry"],
        "boundary_conditions": ["Neutral pH"],
        "validation_plan": {
            "experiment": "factorial membrane experiment",
            "variables": ["roughness", "charge"],
            "predicted_outcome": "interaction effect",
            "falsification_criterion": "no interaction",
        },
        "confidence": 0.95,
        "novelty_score": 0.9,
    }
    normalized = engine._normalize(raw, _evidence())
    assert normalized is not None
    assert normalized["status"] == "ai_hypothesis"
    assert normalized["confidence"] == 0.82
    assert len(normalized["evidence_document_ids"]) == 2

    raw["supporting_evidence_ids"] = [1]
    assert engine._normalize(raw, _evidence()) is None


def test_metadata_only_evidence_caps_ai_confidence() -> None:
    engine = object.__new__(KnowledgeDiscoveryEngine)
    raw = {
        "type": "pattern",
        "title": "Abstract-only pattern",
        "claim": "A tentative pattern appears.",
        "rationale": "Two abstracts mention it.",
        "supporting_evidence_ids": [1, 2],
        "contradicting_evidence_ids": [],
        "confidence": 0.9,
        "novelty_score": 0.5,
    }
    normalized = engine._normalize(raw, _evidence("metadata-only"))
    assert normalized is not None
    assert normalized["confidence"] == 0.45
    assert normalized["status"] == "ai_synthesis"


def test_cross_paper_input_prefers_one_pass_over_every_document() -> None:
    evidence = [
        {"document_id": "doc-a", "quote": "a-1"},
        {"document_id": "doc-a", "quote": "a-2"},
        {"document_id": "doc-b", "quote": "b-1"},
        {"document_id": "doc-c", "quote": "c-1"},
        {"document_id": "doc-b", "quote": "b-2"},
    ]
    selected = KnowledgeDiscoveryEngine._diverse_evidence(evidence, 4)
    assert [item["quote"] for item in selected[:3]] == ["a-1", "b-1", "c-1"]
    assert selected[3]["quote"] == "a-2"

import pytest
from pydantic import ValidationError

from app.schemas import ChatRequest, ImportSelectionRequest


def test_chat_uses_deep_thinking_by_default() -> None:
    payload = ChatRequest(conversation_id="conversation", question="question")

    assert payload.deep_thinking is True


def test_import_selection_deduplicates_ids_and_recalculates_count() -> None:
    payload = ImportSelectionRequest(
        candidate_ids=["candidate-a", "candidate-b", "candidate-a"],
        requested_count=3,
    )

    assert payload.candidate_ids == ["candidate-a", "candidate-b"]
    assert payload.requested_count == 2


def test_import_selection_still_limits_unique_ids_to_200() -> None:
    with pytest.raises(ValidationError):
        ImportSelectionRequest(
            candidate_ids=[f"candidate-{index}" for index in range(201)],
            requested_count=200,
        )

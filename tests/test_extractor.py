import pytest

from app.services.extractor import align_entity, normalize_measure


def test_entity_alignment() -> None:
    assert align_entity("piperazine") == "PIP"
    assert align_entity("均苯三甲酰氯") == "TMC"


@pytest.mark.parametrize(
    ("value", "unit", "predicate", "expected_value", "expected_unit"),
    [
        (10.0, "bar", "pressure", 1.0, "MPa"),
        (100.0, "kPa", "pressure", 0.1, "MPa"),
        (298.15, "K", "temperature", 25.0, "°C"),
        (30.0, "LMH", "flux", 30.0, "L·m⁻²·h⁻¹"),
    ],
)
def test_normalize_measure(value, unit, predicate, expected_value, expected_unit) -> None:
    actual_value, actual_unit = normalize_measure(value, unit, predicate)
    assert actual_value == pytest.approx(expected_value)
    assert actual_unit == expected_unit


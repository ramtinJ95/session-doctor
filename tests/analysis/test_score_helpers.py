from __future__ import annotations

from math import inf, nan

import pytest

from session_doctor.analysis.scoring import capped_count, clamp01, score_feature_value


def test_score_helpers_bound_and_format_scores() -> None:
    assert clamp01(-0.25) == 0.0
    assert clamp01(0.75) == 0.75
    assert clamp01(1.25) == 1.0
    assert capped_count(2, cap=4) == 0.5
    assert capped_count(8, cap=4) == 1.0
    assert capped_count(-1, cap=4) == 0.0
    assert score_feature_value(0.3333) == "0.333"
    assert score_feature_value(1.25) == "1.000"


def test_score_helpers_reject_non_finite_scores() -> None:
    for value in (nan, inf, -inf):
        with pytest.raises(ValueError, match="score value must be finite"):
            clamp01(value)
        with pytest.raises(ValueError, match="score value must be finite"):
            score_feature_value(value)

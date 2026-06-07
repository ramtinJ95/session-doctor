from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import HEALTHY_SCORE_THRESHOLD, NEGATIVE_LABELS
from ..classification_context import ClassificationContext
from ..classification_factories import classification, classification_metadata
from ..timeline import has_assistant_final_answer


def healthy_classification(
    context: ClassificationContext,
    classifications: list[SessionClassification],
) -> SessionClassification | None:
    if any(classification.label in NEGATIVE_LABELS for classification in classifications):
        return None
    if classifications:
        return None
    message_count = context.int_feature("user_message_count") + context.int_feature(
        "assistant_message_count"
    )
    if message_count < 1:
        return None
    if not has_assistant_final_answer(context.bundle):
        return None
    if context.bool_feature("unresolved_ending_signal"):
        return None
    score_features = [
        context.float_feature("friction_score"),
        context.float_feature("stuckness_score"),
        context.float_feature("agent_fit_risk"),
    ]
    if any(score >= HEALTHY_SCORE_THRESHOLD for score in score_features):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="healthy",
        score=1.0 - max(score_features, default=0.0),
        confidence=0.55,
        evidence_event_ids=[],
        evidence_summary=(
            "Session appears clean: no failure, repeat, correction, or unresolved-ending evidence."
        ),
        metadata=classification_metadata(
            rule="healthy_v1",
            score_feature="friction_score/stuckness_score/agent_fit_risk",
            threshold=HEALTHY_SCORE_THRESHOLD,
            contributing_features=[
                "friction_score",
                "stuckness_score",
                "agent_fit_risk",
                "unresolved_ending_signal",
            ],
        ),
    )

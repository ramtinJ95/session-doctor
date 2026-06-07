from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import RESOLVED_AFTER_CORRECTIONS_SCORE
from ..classification_context import ClassificationContext
from ..classification_factories import classification, classification_metadata
from ..timeline import resolved_after_last_correction


def resolved_after_corrections_classification(
    context: ClassificationContext,
) -> SessionClassification | None:
    correction_count = context.int_feature("correction_count")
    if correction_count < 1 or not resolved_after_last_correction(
        context.bundle,
        context.message_features,
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="resolved_after_corrections",
        score=RESOLVED_AFTER_CORRECTIONS_SCORE,
        confidence=0.60,
        evidence_event_ids=context.evidence_event_ids(["correction_marker"]),
        evidence_summary="Session ends with a final answer after correction evidence.",
        metadata=classification_metadata(
            rule="resolved_after_corrections_v2",
            contributing_features=["correction_marker", "assistant_final_answer"],
            fixed_score=RESOLVED_AFTER_CORRECTIONS_SCORE,
        ),
    )

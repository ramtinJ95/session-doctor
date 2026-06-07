from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_context import ClassificationContext
from ..classification_evidence import count_phrase, joined_evidence_summary
from ..classification_factories import classification, classification_metadata
from ..ending import unresolved_stop_or_pause_evidence


def abandoned_or_stopped_classification(
    context: ClassificationContext,
) -> SessionClassification | None:
    triggering_event_ids = unresolved_stop_or_pause_evidence(
        context.bundle,
        context.message_features,
    )
    if not triggering_event_ids:
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="abandoned_or_stopped",
        score=0.65,
        confidence=0.70,
        evidence_event_ids=triggering_event_ids,
        evidence_summary=joined_evidence_summary(
            "Session has unresolved stop/defer evidence",
            [count_phrase(len(triggering_event_ids), "late stop or pause marker")],
        ),
        metadata=classification_metadata(
            rule="abandoned_or_stopped_v1",
            contributing_features=["stop_or_pause_marker", "assistant_final_answer"],
            extra_thresholds={"late_stop_or_pause_marker_count": 1},
        ),
    )

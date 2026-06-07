from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD
from ..classification_context import ClassificationContext
from ..classification_evidence import count_phrase, joined_evidence_summary
from ..classification_factories import classification, classification_metadata


def agent_looping_classification(context: ClassificationContext) -> SessionClassification | None:
    repeat_request_count = context.int_feature("repeat_request_count")
    repeated_command_failure_count = context.int_feature("repeated_command_failure_count")
    same_file_repeated_count = context.int_feature("same_file_edited_repeatedly_count")
    agent_fit_risk = context.float_feature("agent_fit_risk")
    if not (
        (repeat_request_count >= 2 and same_file_repeated_count >= 1)
        or repeated_command_failure_count >= AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="agent_looping",
        score=max(
            agent_fit_risk,
            min(
                1.0,
                0.45
                + 0.15 * repeat_request_count
                + 0.15 * same_file_repeated_count
                + 0.10 * repeated_command_failure_count,
            ),
        ),
        confidence=0.65,
        evidence_event_ids=context.evidence_event_ids(
            [
                "repeat_request_similarity",
                "same_file_edited_repeatedly_count",
                "repeated_command_failure_count",
            ],
        ),
        evidence_summary=joined_evidence_summary(
            "Session has loop evidence",
            [
                count_phrase(repeat_request_count, "repeated user request"),
                count_phrase(same_file_repeated_count, "repeatedly edited file"),
                count_phrase(repeated_command_failure_count, "repeated command failure"),
            ],
        ),
        metadata=classification_metadata(
            rule="agent_looping_v2",
            score_feature="agent_fit_risk",
            contributing_features=[
                "repeat_request_count",
                "same_file_edited_repeatedly_count",
                "repeated_command_failure_count",
            ],
            extra_thresholds={
                "repeat_request_count": 2,
                "same_file_edited_repeatedly_count": 1,
                "repeated_command_failure_count": AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD,
            },
        ),
    )

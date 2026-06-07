from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import USER_STUCK_STUCKNESS_THRESHOLD
from ..classification_context import ClassificationContext
from ..classification_evidence import count_phrase, joined_evidence_summary
from ..classification_factories import classification, classification_metadata


def user_stuck_classification(context: ClassificationContext) -> SessionClassification | None:
    repeat_request_count = context.int_feature("repeat_request_count")
    correction_count = context.int_feature("correction_count")
    frustration_count = context.int_feature("frustration_count")
    unresolved_ending_signal = context.bool_feature("unresolved_ending_signal")
    repeated_command_failure_count = context.int_feature("repeated_command_failure_count")
    same_file_repeated_count = context.int_feature("same_file_edited_repeatedly_count")
    stuckness_score = context.float_feature("stuckness_score")
    has_user_facing_stuck_evidence = (
        repeat_request_count >= 2
        or correction_count >= 2
        or (unresolved_ending_signal and (correction_count > 0 or frustration_count > 0))
        or (
            stuckness_score >= USER_STUCK_STUCKNESS_THRESHOLD
            and (repeat_request_count > 0 or correction_count > 0 or frustration_count > 0)
        )
    )
    if not has_user_facing_stuck_evidence:
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="user_stuck",
        score=max(
            stuckness_score,
            min(
                1.0,
                0.40
                + 0.15 * repeat_request_count
                + 0.15 * correction_count
                + 0.10 * frustration_count
                + (0.20 if unresolved_ending_signal else 0.0),
            ),
        ),
        confidence=0.75,
        evidence_event_ids=context.evidence_event_ids(
            [
                "repeat_request_similarity",
                "correction_marker",
                "frustration_marker",
                "repeated_command_failure_count",
                "same_file_edited_repeatedly_count",
                "unresolved_ending_signal",
            ],
        ),
        evidence_summary=joined_evidence_summary(
            "Session shows stuckness evidence",
            [
                count_phrase(repeat_request_count, "repeated user request"),
                count_phrase(correction_count, "correction"),
                count_phrase(frustration_count, "frustration marker"),
                count_phrase(repeated_command_failure_count, "repeated command failure"),
                count_phrase(same_file_repeated_count, "repeatedly edited file"),
                "unresolved-ending evidence" if unresolved_ending_signal else "",
            ],
        ),
        metadata=classification_metadata(
            rule="user_stuck_v2",
            score_feature="stuckness_score",
            threshold=USER_STUCK_STUCKNESS_THRESHOLD,
            contributing_features=[
                "repeat_request_count",
                "correction_count",
                "frustration_count",
                "repeated_command_failure_count",
                "same_file_edited_repeatedly_count",
                "unresolved_ending_signal",
            ],
        ),
    )

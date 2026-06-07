from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import (
    TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD,
    TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD,
    TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD,
)
from ..classification_context import ClassificationContext
from ..classification_evidence import count_phrase, joined_evidence_summary, ratio_phrase
from ..classification_factories import classification, classification_metadata


def tooling_blocked_classification(context: ClassificationContext) -> SessionClassification | None:
    failed_command_ratio = context.float_feature("failed_command_ratio")
    repeated_failure_count = context.int_feature("repeated_failure_count")
    failed_tool_result_ratio = context.float_feature("failed_tool_result_ratio")
    friction_score = context.float_feature("friction_score")
    if (
        failed_command_ratio < TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD
        and failed_tool_result_ratio < TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD
        and repeated_failure_count < TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="tooling_blocked",
        score=max(
            friction_score,
            failed_command_ratio,
            failed_tool_result_ratio,
            min(1.0, 0.50 + 0.10 * repeated_failure_count),
        ),
        confidence=0.80,
        evidence_event_ids=context.evidence_event_ids(
            ["failed_command_count", "failed_tool_result_count", "repeated_failure_count"],
        ),
        evidence_summary=joined_evidence_summary(
            "Session has tooling blocker evidence",
            [
                ratio_phrase(failed_command_ratio, "failed command ratio"),
                ratio_phrase(failed_tool_result_ratio, "failed tool-result ratio"),
                count_phrase(repeated_failure_count, "repeated failure"),
            ],
        ),
        metadata=classification_metadata(
            rule="tooling_blocked_v2",
            score_feature="friction_score",
            threshold=TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD,
            contributing_features=[
                "failed_command_ratio",
                "failed_tool_result_ratio",
                "repeated_failure_count",
            ],
            extra_thresholds={
                "failed_tool_result_ratio": TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD,
                "repeated_failure_count": TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD,
            },
        ),
    )

from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import (
    REPO_COMPLEXITY_HIGH_THRESHOLD,
    TASK_TOO_LARGE_COMPLEXITY_THRESHOLD,
    TASK_TOO_LARGE_FRICTION_THRESHOLD,
)
from ..classification_context import ClassificationContext
from ..classification_evidence import count_phrase, joined_evidence_summary, ratio_phrase
from ..classification_factories import classification, classification_metadata


def task_too_large_classification(context: ClassificationContext) -> SessionClassification | None:
    project_complexity_signal = context.float_feature("project_complexity_signal")
    friction_score = context.float_feature("friction_score")
    unresolved_ending_signal = context.bool_feature("unresolved_ending_signal")
    edited_file_count = context.int_feature("edited_file_count")
    command_count = context.int_feature("command_count")
    broad_surface = edited_file_count >= 6 and command_count >= 8
    has_friction = friction_score >= TASK_TOO_LARGE_FRICTION_THRESHOLD or unresolved_ending_signal
    if not (
        project_complexity_signal >= TASK_TOO_LARGE_COMPLEXITY_THRESHOLD
        and has_friction
        and (broad_surface or unresolved_ending_signal)
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="task_too_large",
        score=max(project_complexity_signal, friction_score),
        confidence=0.60,
        evidence_event_ids=context.evidence_event_ids(
            [
                "project_complexity_signal",
                "friction_score",
                "edited_file_count",
                "command_count",
                "unresolved_ending_signal",
            ]
        ),
        evidence_summary=joined_evidence_summary(
            "Session has broad task-surface evidence with friction",
            [
                count_phrase(edited_file_count, "edited file"),
                count_phrase(command_count, "command"),
                ratio_phrase(project_complexity_signal, "project complexity signal"),
                ratio_phrase(friction_score, "friction score"),
                "unresolved-ending evidence" if unresolved_ending_signal else "",
            ],
        ),
        metadata=classification_metadata(
            rule="task_too_large_v1",
            score_feature="project_complexity_signal",
            threshold=TASK_TOO_LARGE_COMPLEXITY_THRESHOLD,
            contributing_features=[
                "project_complexity_signal",
                "friction_score",
                "edited_file_count",
                "command_count",
                "unresolved_ending_signal",
            ],
            extra_thresholds={
                "friction_score": TASK_TOO_LARGE_FRICTION_THRESHOLD,
                "unresolved_ending_signal": 1,
                "broad_surface_edited_file_count": 6,
                "broad_surface_command_count": 8,
            },
        ),
    )


def repo_complexity_high_classification(
    context: ClassificationContext,
) -> SessionClassification | None:
    project_complexity_signal = context.float_feature("project_complexity_signal")
    edited_file_count = context.int_feature("edited_file_count")
    same_file_repeated_count = context.int_feature("same_file_edited_repeatedly_count")
    command_count = context.int_feature("command_count")
    tool_result_count = context.int_feature("tool_result_count")
    activity_count = command_count + tool_result_count
    if not (
        project_complexity_signal >= REPO_COMPLEXITY_HIGH_THRESHOLD
        and edited_file_count >= 2
        and same_file_repeated_count >= 1
        and activity_count >= 8
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="repo_complexity_high",
        score=project_complexity_signal,
        confidence=0.55,
        evidence_event_ids=context.evidence_event_ids(
            [
                "project_complexity_signal",
                "edited_file_count",
                "same_file_edited_repeatedly_count",
                "command_count",
                "tool_result_count",
            ]
        ),
        evidence_summary=joined_evidence_summary(
            "Session touched a complex-looking area",
            [
                count_phrase(edited_file_count, "edited file"),
                count_phrase(same_file_repeated_count, "repeatedly edited file"),
                count_phrase(activity_count, "command/tool result"),
                ratio_phrase(project_complexity_signal, "project complexity signal"),
            ],
        ),
        metadata=classification_metadata(
            rule="repo_complexity_high_v1",
            score_feature="project_complexity_signal",
            threshold=REPO_COMPLEXITY_HIGH_THRESHOLD,
            contributing_features=[
                "edited_file_count",
                "same_file_edited_repeatedly_count",
                "max_edits_to_single_file",
                "command_count",
                "tool_result_count",
            ],
        ),
    )

from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    MessageFeature,
    SessionClassification,
    SessionFeature,
)

from .classification_constants import (
    AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD,
    AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD,
    HEALTHY_SCORE_THRESHOLD,
    MISUNDERSTANDING_CORRECTION_FAMILIES,
    NEGATIVE_LABELS,
    PROMPT_AMBIGUOUS_THRESHOLD,
    REPO_COMPLEXITY_HIGH_THRESHOLD,
    RESOLVED_AFTER_CORRECTIONS_SCORE,
    TASK_TOO_LARGE_COMPLEXITY_THRESHOLD,
    TASK_TOO_LARGE_FRICTION_THRESHOLD,
    TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD,
    TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD,
    TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD,
    USER_STUCK_STUCKNESS_THRESHOLD,
)
from .classification_context import (
    ClassificationContext,
    bool_feature,
    float_feature,
    int_feature,
)
from .classification_evidence import (
    count_phrase,
    evidence_event_ids,
    families_phrase,
    joined_evidence_summary,
    ratio_phrase,
)
from .classification_factories import classification, classification_metadata
from .ending import unresolved_stop_or_pause_evidence
from .timeline import (
    has_assistant_final_answer,
    resolved_after_last_correction,
)
from .timeline import (
    has_later_final_answer as timeline_has_later_final_answer,
)

__all__ = [
    "AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD",
    "AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD",
    "ClassificationContext",
    "HEALTHY_SCORE_THRESHOLD",
    "MISUNDERSTANDING_CORRECTION_FAMILIES",
    "NEGATIVE_LABELS",
    "PROMPT_AMBIGUOUS_THRESHOLD",
    "REPO_COMPLEXITY_HIGH_THRESHOLD",
    "RESOLVED_AFTER_CORRECTIONS_SCORE",
    "TASK_TOO_LARGE_COMPLEXITY_THRESHOLD",
    "TASK_TOO_LARGE_FRICTION_THRESHOLD",
    "TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD",
    "TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD",
    "TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD",
    "USER_STUCK_STUCKNESS_THRESHOLD",
    "agent_looping_classification",
    "agent_misunderstood_classification",
    "bool_feature",
    "classification",
    "classification_metadata",
    "classify_session",
    "count_phrase",
    "evidence_event_ids",
    "families_phrase",
    "float_feature",
    "has_later_final_answer",
    "healthy_classification",
    "int_feature",
    "joined_evidence_summary",
    "prompt_ambiguous_classification",
    "ratio_phrase",
    "repo_complexity_high_classification",
    "resolved_after_corrections_classification",
    "task_too_large_classification",
    "tooling_blocked_classification",
    "user_stuck_classification",
]


def classify_session(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    message_features: list[MessageFeature],
    session_features: list[SessionFeature],
) -> list[SessionClassification]:
    if bundle.session is None:
        msg = "Cannot classify a bundle without a session record."
        raise ValueError(msg)

    context = ClassificationContext(
        bundle=bundle,
        analysis_run_id=analysis_run_id,
        message_features=message_features,
        session_features={feature.feature_name: feature for feature in session_features},
    )
    label_rules = (
        user_stuck_classification,
        tooling_blocked_classification,
        agent_looping_classification,
        resolved_after_corrections_classification,
        agent_misunderstood_classification,
        prompt_ambiguous_classification,
        task_too_large_classification,
        repo_complexity_high_classification,
        abandoned_or_stopped_classification,
    )
    classifications = [
        classification for rule in label_rules if (classification := rule(context)) is not None
    ]
    if healthy := healthy_classification(context, classifications):
        classifications.append(healthy)
    return classifications


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


def agent_misunderstood_classification(
    context: ClassificationContext,
) -> SessionClassification | None:
    correction_count = context.int_feature("correction_count")
    prompt_clarity_risk = context.float_feature("prompt_clarity_risk")
    matched_families = context.message_feature_values("correction_marker")
    direct_misunderstanding = bool(matched_families & MISUNDERSTANDING_CORRECTION_FAMILIES)
    if not (
        correction_count >= 1
        and (
            prompt_clarity_risk >= AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD
            or direct_misunderstanding
        )
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="agent_misunderstood",
        score=max(prompt_clarity_risk, 0.50 if direct_misunderstanding else 0.0),
        confidence=0.72 if direct_misunderstanding else 0.62,
        evidence_event_ids=context.evidence_event_ids(["correction_marker", "prompt_clarity_risk"]),
        evidence_summary=joined_evidence_summary(
            "Session has misunderstanding evidence",
            [
                count_phrase(correction_count, "correction"),
                families_phrase(matched_families, "correction marker"),
                ratio_phrase(prompt_clarity_risk, "prompt clarity risk"),
            ],
        ),
        metadata=classification_metadata(
            rule="agent_misunderstood_v1",
            score_feature="prompt_clarity_risk",
            threshold=AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD,
            contributing_features=["correction_marker", "prompt_clarity_risk"],
        ),
    )


def prompt_ambiguous_classification(context: ClassificationContext) -> SessionClassification | None:
    prompt_clarity_risk = context.float_feature("prompt_clarity_risk")
    scope_boundary_count = context.int_feature("scope_boundary_count")
    ambiguity_count = context.int_feature("ambiguity_count")
    failed_command_ratio = context.float_feature("failed_command_ratio")
    failed_tool_result_ratio = context.float_feature("failed_tool_result_ratio")
    if not (
        prompt_clarity_risk >= PROMPT_AMBIGUOUS_THRESHOLD
        and (scope_boundary_count >= 2 or ambiguity_count >= 1)
        and failed_command_ratio < TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD
        and failed_tool_result_ratio < TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD
    ):
        return None

    return classification(
        analysis_run_id=context.analysis_run_id,
        session_id=context.session_id,
        label="prompt_ambiguous",
        score=prompt_clarity_risk,
        confidence=0.66 if ambiguity_count else 0.58,
        evidence_event_ids=context.evidence_event_ids(
            ["scope_boundary_marker", "ambiguity_marker", "prompt_clarity_risk"]
        ),
        evidence_summary=joined_evidence_summary(
            "Session has prompt-clarity risk evidence",
            [
                count_phrase(scope_boundary_count, "scope boundary"),
                count_phrase(ambiguity_count, "ambiguity marker"),
                ratio_phrase(prompt_clarity_risk, "prompt clarity risk"),
            ],
        ),
        metadata=classification_metadata(
            rule="prompt_ambiguous_v1",
            score_feature="prompt_clarity_risk",
            threshold=PROMPT_AMBIGUOUS_THRESHOLD,
            contributing_features=[
                "scope_boundary_count",
                "correction_count",
                "repeat_request_count",
                "ambiguity_count",
            ],
            extra_thresholds={
                "failed_command_ratio": TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD,
                "failed_tool_result_ratio": TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD,
            },
        ),
    )


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


def has_later_final_answer(record_index: int | None, final_answer_indexes: list[int]) -> bool:
    return timeline_has_later_final_answer(record_index, final_answer_indexes)

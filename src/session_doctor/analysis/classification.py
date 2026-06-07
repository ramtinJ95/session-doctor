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
from .classification_rules import (
    abandoned_or_stopped_classification,
    agent_looping_classification,
    agent_misunderstood_classification,
    healthy_classification,
    prompt_ambiguous_classification,
    repo_complexity_high_classification,
    resolved_after_corrections_classification,
    task_too_large_classification,
    tooling_blocked_classification,
    user_stuck_classification,
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


def has_later_final_answer(record_index: int | None, final_answer_indexes: list[int]) -> bool:
    return timeline_has_later_final_answer(record_index, final_answer_indexes)

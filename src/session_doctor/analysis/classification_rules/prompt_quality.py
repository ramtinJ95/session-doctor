from __future__ import annotations

from session_doctor.schemas import SessionClassification

from ..classification_constants import (
    AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD,
    MISUNDERSTANDING_CORRECTION_FAMILIES,
    PROMPT_AMBIGUOUS_THRESHOLD,
    TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD,
    TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD,
)
from ..classification_context import ClassificationContext
from ..classification_evidence import (
    count_phrase,
    families_phrase,
    joined_evidence_summary,
    ratio_phrase,
)
from ..classification_factories import classification, classification_metadata


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

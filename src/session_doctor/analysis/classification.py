from __future__ import annotations

from dataclasses import dataclass

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    MessageFeature,
    SessionClassification,
    SessionFeature,
)

from .ending import unresolved_stop_or_pause_evidence
from .timeline import has_assistant_final_answer, resolved_after_last_correction

USER_STUCK_STUCKNESS_THRESHOLD = 0.45
TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD = 0.50
TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD = 0.50
TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD = 2
AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD = 2
RESOLVED_AFTER_CORRECTIONS_SCORE = 0.70
HEALTHY_SCORE_THRESHOLD = 0.25
AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD = 0.35
PROMPT_AMBIGUOUS_THRESHOLD = 0.55
TASK_TOO_LARGE_COMPLEXITY_THRESHOLD = 0.65
TASK_TOO_LARGE_FRICTION_THRESHOLD = 0.35
REPO_COMPLEXITY_HIGH_THRESHOLD = 0.75
NEGATIVE_LABELS = frozenset(
    {
        "user_stuck",
        "tooling_blocked",
        "agent_looping",
        "agent_misunderstood",
        "prompt_ambiguous",
        "task_too_large",
        "repo_complexity_high",
        "abandoned_or_stopped",
    }
)
MISUNDERSTANDING_CORRECTION_FAMILIES = frozenset(
    {
        "not_what_i_asked",
        "not_what_i_meant",
        "misunderstood",
        "unexpected_action",
    }
)


@dataclass(frozen=True)
class ClassificationContext:
    bundle: ParsedSessionBundle
    analysis_run_id: str
    message_features: list[MessageFeature]
    session_features: dict[str, SessionFeature]

    @property
    def session_id(self) -> str:
        assert self.bundle.session is not None
        return self.bundle.session.session_id

    def int_feature(self, name: str) -> int:
        return int_feature(self.session_features, name)

    def float_feature(self, name: str) -> float:
        return float_feature(self.session_features, name)

    def bool_feature(self, name: str) -> bool:
        return bool_feature(self.session_features, name)

    def evidence_event_ids(self, feature_names: list[str]) -> list[str]:
        return evidence_event_ids(self.message_features, self.session_features, feature_names)

    def message_feature_values(self, feature_name: str) -> set[str]:
        return {
            feature.feature_value
            for feature in self.message_features
            if feature.feature_name == feature_name
        }


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


def classification_metadata(
    *,
    rule: str,
    contributing_features: list[str],
    score_feature: str | None = None,
    threshold: float | int | None = None,
    extra_thresholds: dict[str, float | int] | None = None,
    fixed_score: float | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "rule": rule,
        "contributing_features": contributing_features,
    }
    if score_feature is not None:
        metadata["score_feature"] = score_feature
    if threshold is not None:
        metadata["threshold"] = threshold
    if extra_thresholds:
        metadata["extra_thresholds"] = extra_thresholds
    if fixed_score is not None:
        metadata["fixed_score"] = fixed_score
    return metadata


def joined_evidence_summary(prefix: str, evidence_parts: list[str]) -> str:
    populated_parts = [part for part in evidence_parts if part]
    if not populated_parts:
        return f"{prefix}."
    return f"{prefix}: {', '.join(populated_parts)}."


def count_phrase(count: int, singular_label: str) -> str:
    if count <= 0:
        return ""
    plural_label = singular_label if count == 1 else f"{singular_label}s"
    return f"{count} {plural_label}"


def ratio_phrase(ratio: float, label: str) -> str:
    if ratio <= 0:
        return ""
    return f"{label} {ratio:.2f}"


def families_phrase(families: set[str], label: str) -> str:
    if not families:
        return ""
    return f"{label} families {', '.join(sorted(families))}"


def int_feature(features: dict[str, SessionFeature], name: str) -> int:
    feature = features.get(name)
    if feature is None:
        return 0
    try:
        return int(float(feature.feature_value))
    except ValueError:
        return 0


def float_feature(features: dict[str, SessionFeature], name: str) -> float:
    feature = features.get(name)
    if feature is None:
        return 0.0
    try:
        return float(feature.feature_value)
    except ValueError:
        return 0.0


def bool_feature(features: dict[str, SessionFeature], name: str) -> bool:
    feature = features.get(name)
    return feature is not None and feature.feature_value == "true"


def evidence_event_ids(
    message_features: list[MessageFeature],
    session_features: dict[str, SessionFeature],
    feature_names: list[str],
) -> list[str]:
    event_ids: set[str] = set()
    for feature in message_features:
        if feature.feature_name in feature_names and feature.source_event_id:
            event_ids.add(feature.source_event_id)
    for feature_name in feature_names:
        feature = session_features.get(feature_name)
        if feature is None:
            continue
        raw_event_ids = feature.evidence.get("source_event_ids", [])
        if not isinstance(raw_event_ids, list):
            continue
        event_ids.update(event_id for event_id in raw_event_ids if isinstance(event_id, str))
    return sorted(event_ids)


def classification(
    *,
    analysis_run_id: str,
    session_id: str,
    label: str,
    score: float,
    confidence: float,
    evidence_event_ids: list[str],
    evidence_summary: str,
    metadata: dict[str, object],
) -> SessionClassification:
    return SessionClassification(
        session_classification_id=stable_id(
            "session_classification",
            analysis_run_id,
            session_id,
            label,
        ),
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        label=label,
        score=score,
        confidence=confidence,
        evidence_event_ids=evidence_event_ids,
        evidence_summary=evidence_summary,
        metadata=metadata,
    )

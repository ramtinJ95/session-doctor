from __future__ import annotations

from dataclasses import dataclass

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    MessageFeature,
    NormalizedRole,
    SessionClassification,
    SessionFeature,
)

USER_STUCK_STUCKNESS_THRESHOLD = 0.45
TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD = 0.50
TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD = 2
AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD = 2
RESOLVED_AFTER_CORRECTIONS_SCORE = 0.70


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
    rules = (
        user_stuck_classification,
        tooling_blocked_classification,
        agent_looping_classification,
        resolved_after_corrections_classification,
    )
    return [classification for rule in rules if (classification := rule(context)) is not None]


def user_stuck_classification(context: ClassificationContext) -> SessionClassification | None:
    repeat_request_count = context.int_feature("repeat_request_count")
    correction_count = context.int_feature("correction_count")
    frustration_count = context.int_feature("frustration_count")
    unresolved_ending_signal = context.bool_feature("unresolved_ending_signal")
    stuckness_score = context.float_feature("stuckness_score")
    if not (
        repeat_request_count >= 2
        or correction_count >= 2
        or (unresolved_ending_signal and (correction_count > 0 or frustration_count > 0))
        or stuckness_score >= USER_STUCK_STUCKNESS_THRESHOLD
    ):
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
                "unresolved_ending_signal",
            ],
        ),
        evidence_summary=joined_evidence_summary(
            "Session shows stuckness evidence",
            [
                count_phrase(repeat_request_count, "repeated user request"),
                count_phrase(correction_count, "correction"),
                count_phrase(frustration_count, "frustration marker"),
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
            threshold=AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD,
            contributing_features=[
                "repeat_request_count",
                "same_file_edited_repeatedly_count",
                "repeated_command_failure_count",
            ],
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
            score_feature="resolved_after_corrections_score",
            threshold=RESOLVED_AFTER_CORRECTIONS_SCORE,
            contributing_features=["correction_marker", "assistant_final_answer"],
        ),
    )


def classification_metadata(
    *,
    rule: str,
    score_feature: str,
    threshold: float | int,
    contributing_features: list[str],
    extra_thresholds: dict[str, float | int] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "rule": rule,
        "score_feature": score_feature,
        "threshold": threshold,
        "contributing_features": contributing_features,
    }
    if extra_thresholds:
        metadata["extra_thresholds"] = extra_thresholds
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


def resolved_after_last_correction(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> bool:
    event_indexes = {
        event.event_id: event.record_index
        for event in bundle.raw_events
        if event.event_id is not None
    }
    correction_indexes = [
        event_indexes[feature.source_event_id]
        for feature in message_features
        if feature.feature_name == "correction_marker" and feature.source_event_id in event_indexes
    ]
    if not correction_indexes:
        return False
    last_correction_index = max(correction_indexes)

    final_answer_indexes = [
        event_indexes[message.source_event_id]
        for message in bundle.messages
        if message.role == NormalizedRole.ASSISTANT
        and message.metadata.get("phase") == "final_answer"
        and message.source_event_id in event_indexes
    ]
    if not final_answer_indexes or max(final_answer_indexes) <= last_correction_index:
        return False

    return not any(
        command.source_event_id in event_indexes
        and event_indexes[command.source_event_id] > last_correction_index
        and command.exit_code is not None
        and command.exit_code != 0
        for command in bundle.command_runs
    )


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

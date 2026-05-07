from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    MessageFeature,
    NormalizedRole,
    SessionClassification,
    SessionFeature,
)


def classify_session(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    message_features: list[MessageFeature],
    session_features: list[SessionFeature],
) -> list[SessionClassification]:
    if bundle.session is None:
        msg = "Cannot classify a bundle without a session record."
        raise ValueError(msg)

    feature_values = {feature.feature_name: feature for feature in session_features}
    classifications: list[SessionClassification] = []

    repeat_request_count = int_feature(feature_values, "repeat_request_count")
    correction_count = int_feature(feature_values, "correction_count")
    frustration_count = int_feature(feature_values, "frustration_count")
    failed_command_ratio = float_feature(feature_values, "failed_command_ratio")
    repeated_failure_count = int_feature(feature_values, "repeated_failure_count")
    same_file_repeated_count = int_feature(feature_values, "same_file_edited_repeatedly_count")
    unresolved_ending_signal = bool_feature(feature_values, "unresolved_ending_signal")

    if (
        repeat_request_count >= 2
        or correction_count >= 2
        or (unresolved_ending_signal and (correction_count > 0 or frustration_count > 0))
    ):
        classifications.append(
            classification(
                analysis_run_id=analysis_run_id,
                session_id=bundle.session.session_id,
                label="user_stuck",
                score=min(
                    1.0,
                    0.40
                    + 0.15 * repeat_request_count
                    + 0.15 * correction_count
                    + 0.10 * frustration_count
                    + (0.20 if unresolved_ending_signal else 0.0),
                ),
                confidence=0.75,
                evidence_event_ids=evidence_event_ids(
                    message_features,
                    feature_values,
                    [
                        "repeat_request_similarity",
                        "correction_marker",
                        "frustration_marker",
                        "unresolved_ending_signal",
                    ],
                ),
                evidence_summary=(
                    "Session shows repeated request, correction, frustration, "
                    "or unresolved-ending evidence."
                ),
                metadata={"rule": "user_stuck_v1"},
            )
        )

    if failed_command_ratio >= 0.50 or repeated_failure_count >= 2:
        classifications.append(
            classification(
                analysis_run_id=analysis_run_id,
                session_id=bundle.session.session_id,
                label="tooling_blocked",
                score=max(failed_command_ratio, min(1.0, 0.50 + 0.10 * repeated_failure_count)),
                confidence=0.80,
                evidence_event_ids=evidence_event_ids(
                    message_features,
                    feature_values,
                    ["failed_command_count", "failed_tool_result_count", "repeated_failure_count"],
                ),
                evidence_summary="Session has failed command/tool evidence or repeated failures.",
                metadata={"rule": "tooling_blocked_v1"},
            )
        )

    if (repeat_request_count >= 2 and same_file_repeated_count >= 1) or repeated_failure_count >= 2:
        classifications.append(
            classification(
                analysis_run_id=analysis_run_id,
                session_id=bundle.session.session_id,
                label="agent_looping",
                score=min(
                    1.0,
                    0.45
                    + 0.15 * repeat_request_count
                    + 0.15 * same_file_repeated_count
                    + 0.10 * repeated_failure_count,
                ),
                confidence=0.65,
                evidence_event_ids=evidence_event_ids(
                    message_features,
                    feature_values,
                    [
                        "repeat_request_similarity",
                        "same_file_edited_repeatedly_count",
                        "repeated_failure_count",
                    ],
                ),
                evidence_summary=(
                    "Session has repeated request/file-edit evidence or repeated failures."
                ),
                metadata={"rule": "agent_looping_v1"},
            )
        )

    if correction_count >= 1 and resolved_after_last_correction(bundle, message_features):
        classifications.append(
            classification(
                analysis_run_id=analysis_run_id,
                session_id=bundle.session.session_id,
                label="resolved_after_corrections",
                score=0.70,
                confidence=0.60,
                evidence_event_ids=evidence_event_ids(
                    message_features,
                    feature_values,
                    ["correction_marker"],
                ),
                evidence_summary="Session ends with a final answer after correction evidence.",
                metadata={"rule": "resolved_after_corrections_v1"},
            )
        )

    return classifications


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

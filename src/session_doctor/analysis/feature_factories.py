from __future__ import annotations

from collections import Counter, defaultdict

from session_doctor.ids import stable_id
from session_doctor.schemas import Message, MessageFeature, SessionFeature


def feature_evidence(features: list[MessageFeature], feature_name: str) -> dict[str, object]:
    matched_features = [feature for feature in features if feature.feature_name == feature_name]
    return {
        "message_ids": sorted({feature.message_id for feature in matched_features}),
        "source_event_ids": sorted(
            {feature.source_event_id for feature in matched_features if feature.source_event_id}
        ),
    }


def message_feature_counts(features: list[MessageFeature]) -> Counter[str]:
    feature_message_ids: defaultdict[str, set[str]] = defaultdict(set)
    for feature in features:
        feature_message_ids[feature.feature_name].add(feature.message_id)
    return Counter(
        {
            feature_name: len(message_ids)
            for feature_name, message_ids in feature_message_ids.items()
        }
    )


def message_feature(
    *,
    analysis_run_id: str,
    message: Message,
    feature_name: str,
    feature_value: str,
    score: float = 1.0,
    evidence: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> MessageFeature:
    return MessageFeature(
        message_feature_id=stable_id(
            "message_feature",
            analysis_run_id,
            message.message_id,
            feature_name,
            feature_value,
        ),
        analysis_run_id=analysis_run_id,
        session_id=message.session_id,
        message_id=message.message_id,
        source_event_id=message.source_event_id,
        feature_name=feature_name,
        feature_value=feature_value,
        score=score,
        evidence=evidence or {},
        metadata=metadata or {},
    )


def session_feature(
    analysis_run_id: str,
    session_id: str,
    feature_name: str,
    feature_value: str | int | float | bool,
    *,
    score: float = 1.0,
    evidence: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> SessionFeature:
    return SessionFeature(
        session_feature_id=stable_id("session_feature", analysis_run_id, session_id, feature_name),
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        feature_name=feature_name,
        feature_value=(
            str(feature_value).lower() if isinstance(feature_value, bool) else str(feature_value)
        ),
        score=score,
        evidence=evidence or {},
        metadata=metadata or {},
    )

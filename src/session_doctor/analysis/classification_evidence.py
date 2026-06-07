from __future__ import annotations

from session_doctor.schemas import MessageFeature, SessionFeature


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

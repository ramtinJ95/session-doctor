from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import MessageFeature, NormalizedRole


def event_record_indexes(bundle: ParsedSessionBundle) -> dict[str, int]:
    return {
        event.event_id: event.record_index
        for event in bundle.raw_events
        if event.event_id is not None
    }


def assistant_final_answer_record_indexes(
    bundle: ParsedSessionBundle,
    event_indexes: dict[str, int] | None = None,
) -> list[int]:
    indexes = event_indexes if event_indexes is not None else event_record_indexes(bundle)
    return [
        indexes[message.source_event_id]
        for message in bundle.messages
        if message.role == NormalizedRole.ASSISTANT
        and message.metadata.get("phase") == "final_answer"
        and message.source_event_id in indexes
    ]


def has_later_final_answer(
    record_index: int | None,
    final_answer_indexes: list[int],
) -> bool:
    if record_index is None:
        return False
    return any(final_answer_index > record_index for final_answer_index in final_answer_indexes)


def has_assistant_final_answer(bundle: ParsedSessionBundle) -> bool:
    return any(
        message.role == NormalizedRole.ASSISTANT and message.metadata.get("phase") == "final_answer"
        for message in bundle.messages
    )


def resolved_after_last_correction(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> bool:
    indexes = event_record_indexes(bundle)
    correction_indexes = [
        indexes[feature.source_event_id]
        for feature in message_features
        if feature.feature_name == "correction_marker" and feature.source_event_id in indexes
    ]
    if not correction_indexes:
        return False
    last_correction_index = max(correction_indexes)

    final_answer_indexes = assistant_final_answer_record_indexes(bundle, indexes)
    if not final_answer_indexes or max(final_answer_indexes) <= last_correction_index:
        return False

    return not any(
        command.source_event_id in indexes
        and indexes[command.source_event_id] > last_correction_index
        and command.exit_code is not None
        and command.exit_code != 0
        for command in bundle.command_runs
    )

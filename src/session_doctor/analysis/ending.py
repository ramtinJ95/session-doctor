from __future__ import annotations

from datetime import datetime, timedelta

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import MessageFeature

from .timeline import (
    assistant_final_answer_record_indexes,
    event_record_indexes,
    has_later_final_answer,
)

ENDING_WINDOW_MIN_EVENTS = 5
ENDING_WINDOW_MAX_EVENTS = 20
ENDING_WINDOW_FRACTION = 0.20
ENDING_WINDOW_MINUTES = 10


def unresolved_ending_evidence(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> dict[str, object]:
    late_event_ids = ending_source_event_ids(bundle)
    event_indexes = event_record_indexes(bundle)
    late_record_indexes = {
        event.record_index for event in bundle.raw_events if event.event_id in late_event_ids
    }
    final_answer_indexes = assistant_final_answer_record_indexes(bundle, event_indexes)
    late_message_event_indexes = {
        message.message_id: event_indexes.get(message.source_event_id)
        for message in bundle.messages
        if message.source_event_id in late_event_ids
    }
    unresolved_message_feature_names = {
        "correction_marker",
        "frustration_marker",
        "repeat_request_similarity",
    }
    late_feature_names = {
        feature.feature_name
        for feature in message_features
        if feature.message_id in late_message_event_indexes
        and feature.feature_name in unresolved_message_feature_names
        and not has_later_final_answer(
            late_message_event_indexes[feature.message_id],
            final_answer_indexes,
        )
    }
    late_feature_source_event_ids = {
        feature.source_event_id
        for feature in message_features
        if feature.message_id in late_message_event_indexes
        and feature.source_event_id is not None
        and feature.feature_name in unresolved_message_feature_names
        and not has_later_final_answer(
            late_message_event_indexes[feature.message_id],
            final_answer_indexes,
        )
    }
    late_failed_command_ids = [
        command.command_run_id
        for command in bundle.command_runs
        if command.source_event_id in late_event_ids
        and command.exit_code is not None
        and command.exit_code != 0
        and not has_later_final_answer(
            event_indexes.get(command.source_event_id),
            final_answer_indexes,
        )
    ]
    late_failed_command_source_event_ids = {
        command.source_event_id
        for command in bundle.command_runs
        if command.source_event_id in late_event_ids
        and command.exit_code is not None
        and command.exit_code != 0
        and not has_later_final_answer(
            event_indexes.get(command.source_event_id),
            final_answer_indexes,
        )
    }
    late_warning_ids = [
        warning.warning_id
        for warning in bundle.parse_warnings
        if warning.record_index is not None
        and (
            warning.record_index in late_record_indexes
            or warning.record_index >= ending_record_index_start(bundle)
        )
        and not has_later_final_answer(warning.record_index, final_answer_indexes)
    ]
    evidence: dict[str, object] = {}
    if late_feature_names:
        evidence["late_message_features"] = sorted(late_feature_names)
    if late_failed_command_ids:
        evidence["late_failed_command_ids"] = late_failed_command_ids
    if late_warning_ids:
        evidence["late_parse_warning_ids"] = late_warning_ids
    source_event_ids = sorted(
        event_id
        for event_id in late_feature_source_event_ids | late_failed_command_source_event_ids
        if event_id is not None
    )
    if source_event_ids:
        evidence["source_event_ids"] = source_event_ids
    has_late_unresolved_signal = bool(evidence)
    if not final_answer_indexes and has_late_unresolved_signal:
        evidence["missing_final_answer"] = True
    return evidence


def unresolved_stop_or_pause_evidence(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> list[str]:
    event_indexes = event_record_indexes(bundle)
    late_event_ids = ending_source_event_ids(bundle)
    stop_or_pause_events = [
        (feature.source_event_id, event_indexes[feature.source_event_id])
        for feature in message_features
        if feature.feature_name == "stop_or_pause_marker"
        and feature.source_event_id in late_event_ids
        and feature.source_event_id in event_indexes
    ]
    if not stop_or_pause_events:
        return []

    final_answer_indexes = assistant_final_answer_record_indexes(bundle, event_indexes)
    return sorted(
        {
            source_event_id
            for source_event_id, stop_or_pause_index in stop_or_pause_events
            if source_event_id is not None
            and not has_later_final_answer(stop_or_pause_index, final_answer_indexes)
        }
    )


def ending_source_event_ids(bundle: ParsedSessionBundle) -> set[str]:
    start_index = ending_record_index_start(bundle)
    event_ids = {
        event.event_id
        for event in bundle.raw_events
        if event.record_index >= start_index and event.event_id is not None
    }
    event_ids.update(timestamp_window_source_event_ids(bundle))
    return event_ids


def ending_record_index_start(bundle: ParsedSessionBundle) -> int:
    if not bundle.raw_events:
        return 0
    event_count = len(bundle.raw_events)
    window_size = min(
        ENDING_WINDOW_MAX_EVENTS,
        max(ENDING_WINDOW_MIN_EVENTS, int(event_count * ENDING_WINDOW_FRACTION)),
    )
    max_index = max(event.record_index for event in bundle.raw_events)
    return max(0, max_index - window_size + 1)


def timestamp_window_source_event_ids(bundle: ParsedSessionBundle) -> set[str]:
    timestamps = [event.timestamp for event in bundle.raw_events if event.timestamp is not None]
    if not timestamps:
        return set()
    latest_timestamp = max(timestamps)
    if not isinstance(latest_timestamp, datetime):
        return set()
    cutoff = latest_timestamp - timedelta(minutes=ENDING_WINDOW_MINUTES)
    return {
        event.event_id
        for event in bundle.raw_events
        if event.timestamp is not None and event.timestamp >= cutoff
    }

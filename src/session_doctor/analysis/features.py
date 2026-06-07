from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    CommandRun,
    Message,
    MessageFeature,
    NormalizedRole,
    SessionFeature,
    ToolResult,
)

from . import ending as ending_helpers
from .feature_factories import (
    feature_evidence,
    message_feature,
    message_feature_counts,
    session_feature,
)
from .feature_models import ExtractedFeatures, RequestSignature, SessionFeatureContext
from .file_features import (
    MUTATING_FILE_OPERATIONS,
    file_activity_session_features,
    file_edit_source_events,
    max_file_edit_evidence,
    repeated_file_edit_source_events,
)
from .markers import (
    AMBIGUITY_MARKERS,
    CORRECTION_MARKERS,
    FRUSTRATION_MARKERS,
    SCOPE_BOUNDARY_MARKERS,
    STOP_OR_PAUSE_CONTEXT_PATTERN,
    STOP_OR_PAUSE_MARKERS,
    marker_features,
    marker_matches,
    marker_matches_for_feature,
    normalized_marker_text,
)
from .repeated_failures import (
    repeated_command_loop_failure_groups,
    repeated_failure_groups,
    repeated_failure_max_repeat_count,
    repeated_failure_source_event_ids,
)
from .scoring import (
    SESSION_FEATURE_EVIDENCE_ALIASES,
    bool_session_feature,
    capped_count,
    clamp01,
    evidence_feature_names,
    float_session_feature,
    int_session_feature,
    risk_score_feature,
    risk_score_session_features,
    round_score_mapping,
    score_feature_value,
    source_event_ids_for_session_features,
)
from .session_counts import (
    command_session_features,
    count_messages,
    message_count_session_features,
    ratio,
    repeated_failure_session_features,
    tool_result_session_features,
    unresolved_ending_session_feature,
)
from .similarity import (
    EXACT_NORMALIZED_TEXT_BOOST,
    MINIMUM_COMPARABLE_TOKEN_COUNT,
    REPEAT_REQUEST_SIMILARITY_THRESHOLD,
    STOPWORDS,
    SYNONYMS,
    canonical_token,
    char_grams,
    jaccard,
    normalize_request_text,
    repeated_request_features,
    request_signature,
    request_similarity,
    salient_overlap,
    signature_similarity,
    token_is_salient,
)

ENDING_WINDOW_MIN_EVENTS = ending_helpers.ENDING_WINDOW_MIN_EVENTS
ENDING_WINDOW_MAX_EVENTS = ending_helpers.ENDING_WINDOW_MAX_EVENTS
ENDING_WINDOW_FRACTION = ending_helpers.ENDING_WINDOW_FRACTION
ENDING_WINDOW_MINUTES = ending_helpers.ENDING_WINDOW_MINUTES

__all__ = [
    "AMBIGUITY_MARKERS",
    "CORRECTION_MARKERS",
    "CommandRun",
    "ENDING_WINDOW_FRACTION",
    "ENDING_WINDOW_MAX_EVENTS",
    "ENDING_WINDOW_MIN_EVENTS",
    "ENDING_WINDOW_MINUTES",
    "EXACT_NORMALIZED_TEXT_BOOST",
    "ExtractedFeatures",
    "FRUSTRATION_MARKERS",
    "MINIMUM_COMPARABLE_TOKEN_COUNT",
    "MUTATING_FILE_OPERATIONS",
    "Message",
    "MessageFeature",
    "NormalizedRole",
    "ParsedSessionBundle",
    "REPEAT_REQUEST_SIMILARITY_THRESHOLD",
    "RequestSignature",
    "SCOPE_BOUNDARY_MARKERS",
    "SESSION_FEATURE_EVIDENCE_ALIASES",
    "STOPWORDS",
    "STOP_OR_PAUSE_CONTEXT_PATTERN",
    "STOP_OR_PAUSE_MARKERS",
    "SYNONYMS",
    "SessionFeature",
    "SessionFeatureContext",
    "ToolResult",
    "analyze_features",
    "bool_session_feature",
    "canonical_token",
    "capped_count",
    "char_grams",
    "clamp01",
    "command_session_features",
    "count_messages",
    "dataclass",
    "defaultdict",
    "ending_record_index_start",
    "ending_source_event_ids",
    "evidence_feature_names",
    "feature_evidence",
    "file_activity_session_features",
    "file_edit_source_events",
    "float_session_feature",
    "has_later_final_answer",
    "int_session_feature",
    "isfinite",
    "jaccard",
    "marker_features",
    "marker_matches",
    "marker_matches_for_feature",
    "max_file_edit_evidence",
    "message_count_session_features",
    "message_feature",
    "message_feature_counts",
    "normalize_request_text",
    "normalized_marker_text",
    "ratio",
    "re",
    "repeated_command_loop_failure_groups",
    "repeated_failure_groups",
    "repeated_failure_max_repeat_count",
    "repeated_failure_session_features",
    "repeated_failure_source_event_ids",
    "repeated_file_edit_source_events",
    "repeated_request_features",
    "request_signature",
    "request_similarity",
    "risk_score_feature",
    "risk_score_session_features",
    "round_score_mapping",
    "salient_overlap",
    "score_feature_value",
    "session_count_features",
    "session_feature",
    "session_feature_context",
    "signature_similarity",
    "source_event_ids_for_session_features",
    "stable_id",
    "timestamp_window_source_event_ids",
    "token_is_salient",
    "tool_result_session_features",
    "unresolved_ending_evidence",
    "unresolved_ending_session_feature",
]


def analyze_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
) -> ExtractedFeatures:
    if bundle.session is None:
        msg = "Cannot analyze features without a session record."
        raise ValueError(msg)

    message_features: list[MessageFeature] = []
    message_features.extend(repeated_request_features(bundle.messages, analysis_run_id))
    message_features.extend(marker_features(bundle.messages, analysis_run_id))

    session_features = session_count_features(bundle, analysis_run_id, message_features)
    return ExtractedFeatures(
        message_features=message_features,
        session_features=session_features,
    )


def session_count_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    message_features: list[MessageFeature],
) -> list[SessionFeature]:
    assert bundle.session is not None
    context = session_feature_context(bundle, message_features)
    base_features = [
        *message_count_session_features(bundle, analysis_run_id, context),
        *command_session_features(bundle, analysis_run_id, context),
        *tool_result_session_features(bundle, analysis_run_id, context),
        *repeated_failure_session_features(analysis_run_id, context),
        *file_activity_session_features(analysis_run_id, context),
        unresolved_ending_session_feature(analysis_run_id, context),
    ]
    return [
        *base_features,
        *risk_score_session_features(analysis_run_id, context, base_features),
    ]


def session_feature_context(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> SessionFeatureContext:
    assert bundle.session is not None
    feature_counts = message_feature_counts(message_features)
    failed_commands = [
        command
        for command in bundle.command_runs
        if command.exit_code is not None and command.exit_code != 0
    ]
    failed_tool_results = [result for result in bundle.tool_results if result.is_error is True]
    repeated_failures = repeated_failure_groups(bundle)
    repeated_command_failures = repeated_command_loop_failure_groups(repeated_failures)
    file_edit_counts = Counter(
        activity.path
        for activity in bundle.file_activities
        if activity.operation in MUTATING_FILE_OPERATIONS
    )
    file_edit_events = file_edit_source_events(bundle)
    repeated_file_edits = {path: count for path, count in file_edit_counts.items() if count > 1}
    return SessionFeatureContext(
        session_id=bundle.session.session_id,
        message_features=message_features,
        message_feature_counts=feature_counts,
        failed_commands=failed_commands,
        failed_tool_results=failed_tool_results,
        repeated_failures=repeated_failures,
        repeated_command_failures=repeated_command_failures,
        repeated_command_failure_count=repeated_failure_max_repeat_count(
            repeated_command_failures,
        ),
        file_edit_counts=file_edit_counts,
        file_edit_events=file_edit_events,
        repeated_file_edits=repeated_file_edits,
        repeated_file_edit_events=repeated_file_edit_source_events(
            file_edit_events,
            file_edit_counts,
        ),
        unresolved_evidence=unresolved_ending_evidence(bundle, message_features),
    )


def unresolved_ending_evidence(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> dict[str, object]:
    return ending_helpers.unresolved_ending_evidence(bundle, message_features)


def has_later_final_answer(
    record_index: int | None,
    final_answer_indexes: list[int],
) -> bool:
    return ending_helpers.has_later_final_answer(record_index, final_answer_indexes)


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

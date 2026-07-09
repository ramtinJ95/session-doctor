from __future__ import annotations

from collections import Counter

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import MessageFeature, SessionFeature

from .ending import unresolved_ending_evidence
from .feature_factories import message_feature_counts
from .feature_models import ExtractedFeatures, SessionFeatureContext
from .file_features import (
    MUTATING_FILE_OPERATIONS,
    file_activity_identity,
    file_activity_session_features,
    file_edit_source_events,
    repeated_file_edit_source_events,
)
from .markers import marker_features
from .repeated_failures import (
    repeated_command_loop_failure_groups,
    repeated_failure_groups,
    repeated_failure_max_repeat_count,
)
from .scoring import risk_score_session_features
from .session_counts import (
    command_session_features,
    message_count_session_features,
    repeated_failure_session_features,
    tool_result_session_features,
    unresolved_ending_session_feature,
)
from .similarity import repeated_request_features

__all__ = [
    "analyze_features",
    "session_count_features",
    "session_feature_context",
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
        file_activity_identity(activity)
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

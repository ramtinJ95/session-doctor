from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import Message, NormalizedRole, SessionFeature

from .feature_factories import feature_evidence, session_feature
from .feature_models import SessionFeatureContext
from .repeated_failures import repeated_failure_source_event_ids


def message_count_session_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "user_message_count",
            count_messages(bundle.messages, NormalizedRole.USER),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "assistant_message_count",
            count_messages(bundle.messages, NormalizedRole.ASSISTANT),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "repeat_request_count",
            context.message_feature_counts["repeat_request_similarity"],
            evidence=feature_evidence(context.message_features, "repeat_request_similarity"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "correction_count",
            context.message_feature_counts["correction_marker"],
            evidence=feature_evidence(context.message_features, "correction_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "frustration_count",
            context.message_feature_counts["frustration_marker"],
            evidence=feature_evidence(context.message_features, "frustration_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "scope_boundary_count",
            context.message_feature_counts["scope_boundary_marker"],
            evidence=feature_evidence(context.message_features, "scope_boundary_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "ambiguity_count",
            context.message_feature_counts["ambiguity_marker"],
            evidence=feature_evidence(context.message_features, "ambiguity_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "stop_or_pause_count",
            context.message_feature_counts["stop_or_pause_marker"],
            evidence=feature_evidence(context.message_features, "stop_or_pause_marker"),
        ),
    ]


def command_session_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    failed_command_ratio = ratio(len(context.failed_commands), len(bundle.command_runs))
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "command_count",
            len(bundle.command_runs),
            evidence={
                "command_run_ids": [command.command_run_id for command in bundle.command_runs],
                "source_event_ids": [
                    command.source_event_id
                    for command in bundle.command_runs
                    if command.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_command_count",
            len(context.failed_commands),
            evidence={
                "command_run_ids": [command.command_run_id for command in context.failed_commands],
                "source_event_ids": [
                    command.source_event_id
                    for command in context.failed_commands
                    if command.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_command_ratio",
            failed_command_ratio,
            score=failed_command_ratio,
        ),
    ]


def tool_result_session_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    failed_tool_result_ratio = ratio(len(context.failed_tool_results), len(bundle.tool_results))
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "tool_result_count",
            len(bundle.tool_results),
            evidence={
                "tool_result_ids": [result.tool_result_id for result in bundle.tool_results],
                "source_event_ids": [
                    result.source_event_id
                    for result in bundle.tool_results
                    if result.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_tool_result_count",
            len(context.failed_tool_results),
            evidence={
                "tool_result_ids": [
                    result.tool_result_id for result in context.failed_tool_results
                ],
                "source_event_ids": [
                    result.source_event_id
                    for result in context.failed_tool_results
                    if result.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_tool_result_ratio",
            failed_tool_result_ratio,
            score=failed_tool_result_ratio,
        ),
    ]


def repeated_failure_session_features(
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "repeated_failure_count",
            sum(group["repeat_count"] for group in context.repeated_failures),
            evidence={
                "groups": context.repeated_failures,
                "source_event_ids": repeated_failure_source_event_ids(context.repeated_failures),
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "repeated_command_failure_count",
            context.repeated_command_failure_count,
            evidence={
                "groups": context.repeated_command_failures,
                "source_event_ids": repeated_failure_source_event_ids(
                    context.repeated_command_failures
                ),
            },
        ),
    ]


def unresolved_ending_session_feature(
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> SessionFeature:
    return session_feature(
        analysis_run_id,
        context.session_id,
        "unresolved_ending_signal",
        bool(context.unresolved_evidence),
        score=1.0 if context.unresolved_evidence else 0.0,
        evidence=context.unresolved_evidence,
    )


def count_messages(messages: list[Message], role: NormalizedRole) -> int:
    return sum(1 for message in messages if message.role == role)


def ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator

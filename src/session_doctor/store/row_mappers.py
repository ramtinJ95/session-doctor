from __future__ import annotations

import json
from typing import Any

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    AnalysisRun,
    MessageFeature,
    SessionClassification,
    SessionFeature,
)

from .json_values import metadata_json


def session_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    if bundle.session is None:
        return []
    session = bundle.session
    return [
        {
            "session_id": session.session_id,
            "source_id": session.source_id,
            "agent_name": session.agent_name.value,
            "native_session_id": session.native_session_id,
            "parent_session_id": session.parent_session_id,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "cwd": session.cwd,
            "project_path": session.project_path,
            "agent_version": session.agent_version,
            "model_provider": session.model_provider,
            "model": session.model,
            "is_sidechain": session.is_sidechain,
            "metadata_json": metadata_json(session.metadata),
        }
    ]


def raw_event_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event.event_id,
            "source_id": event.source_id,
            "agent_name": event.agent_name.value,
            "record_index": event.record_index,
            "native_event_type": event.native_event_type,
            "native_event_id": event.native_event_id,
            "native_parent_id": event.native_parent_id,
            "timestamp": event.timestamp,
            "payload_hash": event.payload_hash,
            "metadata_json": metadata_json(event.metadata),
        }
        for event in bundle.raw_events
    ]


def message_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "message_id": message.message_id,
            "session_id": message.session_id,
            "role": message.role.value,
            "source_event_id": message.source_event_id,
            "native_message_id": message.native_message_id,
            "parent_message_id": message.parent_message_id,
            "timestamp": message.timestamp,
            "text": message.text,
            "text_hash": message.text_hash,
            "text_length": message.text_length,
            "content_block_types_json": json.dumps(message.content_block_types),
            "metadata_json": metadata_json(message.metadata),
        }
        for message in bundle.messages
    ]


def tool_call_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "tool_call_id": tool_call.tool_call_id,
            "session_id": tool_call.session_id,
            "source_event_id": tool_call.source_event_id,
            "native_tool_call_id": tool_call.native_tool_call_id,
            "name": tool_call.name,
            "timestamp": tool_call.timestamp,
            "arguments_hash": tool_call.arguments_hash,
            "metadata_json": metadata_json(tool_call.metadata),
        }
        for tool_call in bundle.tool_calls
    ]


def tool_result_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "tool_result_id": tool_result.tool_result_id,
            "session_id": tool_result.session_id,
            "tool_call_id": tool_result.tool_call_id,
            "source_event_id": tool_result.source_event_id,
            "native_tool_call_id": tool_result.native_tool_call_id,
            "timestamp": tool_result.timestamp,
            "is_error": tool_result.is_error,
            "output_hash": tool_result.output_hash,
            "output_length": tool_result.output_length,
            "metadata_json": metadata_json(tool_result.metadata),
        }
        for tool_result in bundle.tool_results
    ]


def command_run_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "command_run_id": command_run.command_run_id,
            "session_id": command_run.session_id,
            "source_event_id": command_run.source_event_id,
            "tool_call_id": command_run.tool_call_id,
            "command": command_run.command,
            "command_identity_hash": command_run.command_identity_hash,
            "command_display": command_run.command_display,
            "command_normalization": command_run.command_normalization,
            "cwd": command_run.cwd,
            "started_at": command_run.started_at,
            "ended_at": command_run.ended_at,
            "exit_code": command_run.exit_code,
            "stdout_hash": command_run.stdout_hash,
            "stderr_hash": command_run.stderr_hash,
            "output_length": command_run.output_length,
            "metadata_json": metadata_json(command_run.metadata),
        }
        for command_run in bundle.command_runs
    ]


def file_activity_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "file_activity_id": file_activity.file_activity_id,
            "session_id": file_activity.session_id,
            "source_event_id": file_activity.source_event_id,
            "path": file_activity.path,
            "normalized_path": file_activity.normalized_path,
            "canonical_path": file_activity.canonical_path,
            "project_relative_path": file_activity.project_relative_path,
            "path_resolution": file_activity.path_resolution,
            "operation": file_activity.operation,
            "timestamp": file_activity.timestamp,
            "content_hash": file_activity.content_hash,
            "metadata_json": metadata_json(file_activity.metadata),
        }
        for file_activity in bundle.file_activities
    ]


def model_usage_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "model_usage_id": usage.model_usage_id,
            "session_id": usage.session_id,
            "source_event_id": usage.source_event_id,
            "timestamp": usage.timestamp,
            "provider": usage.provider,
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "total_tokens": usage.total_tokens,
            "cost": usage.cost,
            "aggregation_semantics": usage.aggregation_semantics.value,
            "metadata_json": metadata_json(usage.metadata),
        }
        for usage in bundle.model_usage
    ]


def parse_warning_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "warning_id": warning.warning_id,
            "source_id": warning.source_id,
            "record_index": warning.record_index,
            "severity": warning.severity,
            "message": warning.message,
            "metadata_json": metadata_json(warning.metadata),
        }
        for warning in bundle.parse_warnings
    ]


def analysis_run_rows(analysis_run: AnalysisRun) -> list[dict[str, Any]]:
    return [
        {
            "analysis_run_id": analysis_run.analysis_run_id,
            "session_id": analysis_run.session_id,
            "started_at": analysis_run.started_at,
            "completed_at": analysis_run.completed_at,
            "analyzer_version": analysis_run.analyzer_version,
            "artifact_path": analysis_run.artifact_path,
            "metadata_json": metadata_json(analysis_run.metadata),
        }
    ]


def message_feature_rows(features: list[MessageFeature]) -> list[dict[str, Any]]:
    return [
        {
            "message_feature_id": feature.message_feature_id,
            "analysis_run_id": feature.analysis_run_id,
            "session_id": feature.session_id,
            "message_id": feature.message_id,
            "source_event_id": feature.source_event_id,
            "feature_name": feature.feature_name,
            "feature_value": feature.feature_value,
            "score": feature.score,
            "evidence_json": metadata_json(feature.evidence),
            "metadata_json": metadata_json(feature.metadata),
        }
        for feature in features
    ]


def session_feature_rows(features: list[SessionFeature]) -> list[dict[str, Any]]:
    return [
        {
            "session_feature_id": feature.session_feature_id,
            "analysis_run_id": feature.analysis_run_id,
            "session_id": feature.session_id,
            "feature_name": feature.feature_name,
            "feature_value": feature.feature_value,
            "score": feature.score,
            "evidence_json": metadata_json(feature.evidence),
            "metadata_json": metadata_json(feature.metadata),
        }
        for feature in features
    ]


def session_classification_rows(
    classifications: list[SessionClassification],
) -> list[dict[str, Any]]:
    return [
        {
            "session_classification_id": classification.session_classification_id,
            "analysis_run_id": classification.analysis_run_id,
            "session_id": classification.session_id,
            "label": classification.label,
            "score": classification.score,
            "confidence": classification.confidence,
            "evidence_event_ids_json": json.dumps(classification.evidence_event_ids),
            "evidence_summary": classification.evidence_summary,
            "metadata_json": metadata_json(classification.metadata),
        }
        for classification in classifications
    ]

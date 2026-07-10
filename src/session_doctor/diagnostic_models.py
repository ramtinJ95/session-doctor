from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    MessageFeature,
    ModelUsage,
    ParseWarning,
    RawEvent,
    Session,
    SessionClassification,
    SessionFeature,
    ToolCall,
    ToolResult,
)


class AnalysisCompatibility(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"


@dataclass(frozen=True)
class NormalizedSessionData:
    session: Session
    raw_events: tuple[RawEvent, ...]
    messages: tuple[Message, ...]
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    command_runs: tuple[CommandRun, ...]
    file_activities: tuple[FileActivity, ...]
    model_usage: tuple[ModelUsage, ...]
    parse_warnings: tuple[ParseWarning, ...]


@dataclass(frozen=True)
class TopologyReference:
    session_id: str
    relationship: Literal["parent", "child"]
    agent_name: AgentName | None
    is_sidechain: bool | None
    exists: bool


@dataclass(frozen=True)
class DiagnosticAnalysis:
    compatibility: AnalysisCompatibility
    current_analyzer_version: str
    observed_analyzer_version: str | None
    analysis_run_id: str | None
    action: str | None
    message_features: tuple[MessageFeature, ...]
    session_features: tuple[SessionFeature, ...]
    classifications: tuple[SessionClassification, ...]


@dataclass(frozen=True)
class DiagnosticIndexes:
    raw_events_by_id: Mapping[str, RawEvent]
    raw_events_by_record_index: Mapping[int, tuple[RawEvent, ...]]
    messages_by_id: Mapping[str, Message]
    tool_calls_by_id: Mapping[str, ToolCall]
    tool_results_by_id: Mapping[str, ToolResult]
    command_runs_by_id: Mapping[str, CommandRun]
    file_activities_by_id: Mapping[str, FileActivity]


@dataclass(frozen=True)
class UnresolvedDiagnosticReferences:
    message_source_event_ids: tuple[str, ...] = ()
    message_parent_ids: tuple[str, ...] = ()
    tool_call_source_event_ids: tuple[str, ...] = ()
    tool_result_source_event_ids: tuple[str, ...] = ()
    tool_result_tool_call_ids: tuple[str, ...] = ()
    command_source_event_ids: tuple[str, ...] = ()
    command_tool_call_ids: tuple[str, ...] = ()
    file_source_event_ids: tuple[str, ...] = ()
    warning_ids: tuple[str, ...] = ()
    message_feature_message_ids: tuple[str, ...] = ()
    message_feature_source_event_ids: tuple[str, ...] = ()
    classification_source_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticSnapshot:
    normalized: NormalizedSessionData
    topology_references: tuple[TopologyReference, ...]
    analysis: DiagnosticAnalysis
    indexes: DiagnosticIndexes
    unresolved: UnresolvedDiagnosticReferences


def immutable_index[Value](values: Mapping[str, Value]) -> Mapping[str, Value]:
    return MappingProxyType(dict(values))


def immutable_record_index(
    values: Mapping[int, tuple[RawEvent, ...]],
) -> Mapping[int, tuple[RawEvent, ...]]:
    return MappingProxyType(dict(values))

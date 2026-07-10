from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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
class RecurrenceFamilyExclusions:
    orphan_parent: int = 0
    cycle: int = 0
    cross_agent_parent: int = 0


@dataclass(frozen=True)
class RecurrenceTemporalExclusions:
    untimed_sessions: int = 0
    before_window_sessions: int = 0
    after_cutoff_sessions: int = 0
    untimed_events: int = 0
    before_window_events: int = 0
    after_cutoff_events: int = 0


@dataclass(frozen=True)
class RecurrenceAnalysisExclusions:
    stale: int = 0
    missing: int = 0


@dataclass(frozen=True)
class RecurrenceEvidence:
    event_count: int
    selected_session_event_count: int
    session_count: int
    root_family_count: int
    top_level_session_count: int
    sidechain_session_count: int
    agents: tuple[str, ...]
    first_at: datetime
    most_recent_at: datetime


@dataclass(frozen=True)
class DiagnosticFailedCommandPattern:
    pattern_id: str
    command_display: str
    evidence: RecurrenceEvidence


@dataclass(frozen=True)
class DiagnosticFailedToolPattern:
    pattern_id: str
    tool_name: str
    fingerprint: str
    evidence: RecurrenceEvidence


@dataclass(frozen=True)
class DiagnosticProblematicFilePattern:
    pattern_id: str
    display_path: str
    evidence: RecurrenceEvidence


@dataclass(frozen=True)
class DiagnosticRecurrenceContext:
    status: Literal["available", "unavailable"]
    reason: str | None
    scope_path: str | None
    scope_source: str | None
    window_start: datetime | None
    evidence_cutoff: datetime | None
    family_exclusions: RecurrenceFamilyExclusions
    temporal_exclusions: RecurrenceTemporalExclusions
    problematic_file_analysis_exclusions: RecurrenceAnalysisExclusions
    problematic_files_status: Literal["available", "unavailable"]
    problematic_files_reason: str | None
    failed_commands: tuple[DiagnosticFailedCommandPattern, ...]
    failed_tool_results: tuple[DiagnosticFailedToolPattern, ...]
    problematic_files: tuple[DiagnosticProblematicFilePattern, ...]


@dataclass(frozen=True)
class DiagnosticSnapshot:
    normalized: NormalizedSessionData
    topology_references: tuple[TopologyReference, ...]
    analysis: DiagnosticAnalysis
    indexes: DiagnosticIndexes
    unresolved: UnresolvedDiagnosticReferences
    recurrence: DiagnosticRecurrenceContext


def immutable_index[Value](values: Mapping[str, Value]) -> Mapping[str, Value]:
    return MappingProxyType(dict(values))


def immutable_record_index(
    values: Mapping[int, tuple[RawEvent, ...]],
) -> Mapping[int, tuple[RawEvent, ...]]:
    return MappingProxyType(dict(values))

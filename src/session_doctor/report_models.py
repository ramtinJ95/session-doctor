from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from session_doctor.schemas.common import SessionDoctorModel


class ReportModel(SessionDoctorModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportSession(ReportModel):
    session_id: str
    agent: str
    is_sidechain: bool
    parent_session_id: str | None
    child_session_ids: list[str]
    started_at: datetime | None
    ended_at: datetime | None
    project_hint: str | None
    project_hint_source: str | None
    model_provider: str | None
    model: str | None
    agent_version: str | None


class ReportPrivacy(ReportModel):
    message_text_included: bool
    disclosure_scope: Literal["none", "displayed_evidence_messages"]


class ReportAnalysis(ReportModel):
    status: Literal["current", "stale", "missing"]
    current_analyzer_version: str
    observed_analyzer_version: str | None
    analysis_run_id: str | None
    action: str | None


class ReportSummary(ReportModel):
    raw_events: int
    messages: int
    tool_calls: int
    tool_results: int
    command_runs: int
    file_activities: int
    parse_warnings: int
    parse_warning_codes: list[str]


class ReportScore(ReportModel):
    name: str
    value: float
    component_values: dict[str, float]
    component_weights: dict[str, float]
    contributions: dict[str, float]
    source_event_ids: list[str]
    unresolved_source_event_ids: list[str]


class ReportClassification(ReportModel):
    classification_id: str
    label: str
    score: float
    confidence: float
    evidence_summary: str
    source_event_ids: list[str]
    unresolved_source_event_ids: list[str]


class MessageSignalEvidence(ReportModel):
    item_type: Literal["message_signal"] = "message_signal"
    evidence_id: str
    feature_id: str
    feature_name: str
    message_id: str
    source_event_id: str | None
    role: str | None
    timestamp: datetime | None
    score: float
    matched_message_id: str | None
    matched_source_event_id: str | None
    similarity_score: float | None
    text: str | None


class CommandFailureEvidence(ReportModel):
    item_type: Literal["command_failure"] = "command_failure"
    evidence_id: str
    command_run_id: str
    source_event_id: str | None
    command_display: str
    exit_code: int | None
    fingerprint: str


class ToolFailureEvidence(ReportModel):
    item_type: Literal["tool_failure"] = "tool_failure"
    evidence_id: str
    tool_result_id: str
    tool_call_id: str | None
    source_event_id: str | None
    tool_name: str | None
    output_length: int | None
    fingerprint: str | None


class FailureGroupEvidence(ReportModel):
    item_type: Literal["failure_group"] = "failure_group"
    evidence_id: str
    group_type: str
    fingerprint: str
    occurrence_count: int
    record_ids: list[str]
    source_event_ids: list[str]


class FileLoopEvidence(ReportModel):
    item_type: Literal["file_loop"] = "file_loop"
    evidence_id: str
    display_path: str
    path_resolution: str
    edit_count: int
    file_activity_ids: list[str]
    source_event_ids: list[str]


class ClassificationReferenceEvidence(ReportModel):
    item_type: Literal["classification_reference"] = "classification_reference"
    evidence_id: str
    classification_id: str
    source_event_id: str
    resolved_node_type: Literal["raw_event"] | None
    resolved_node_id: str | None


EvidenceItem = Annotated[
    MessageSignalEvidence
    | CommandFailureEvidence
    | ToolFailureEvidence
    | FailureGroupEvidence
    | FileLoopEvidence
    | ClassificationReferenceEvidence,
    Field(discriminator="item_type"),
]


class BoundedEvidence(ReportModel):
    status: Literal["available", "unavailable"]
    reason: str | None
    total: int
    displayed: int
    omitted: int
    items: list[EvidenceItem]


class ReportEnding(ReportModel):
    status: Literal["available", "unavailable"]
    reason: str | None
    unresolved_ending_signal: bool | None
    evidence_categories: list[str]
    source_event_ids: list[str]
    unresolved_source_event_ids: list[str]
    late_failed_command_ids: list[str]
    late_parse_warning_ids: list[str]
    missing_final_answer: bool | None
    resolution_labels: list[str]


class ReportPatternEvidence(ReportModel):
    pattern_id: str
    event_count: int
    selected_session_event_count: int
    session_count: int
    root_family_count: int
    top_level_session_count: int
    sidechain_session_count: int
    agents: list[str]
    first_at: datetime
    most_recent_at: datetime


class FailedCommandPatternReport(ReportPatternEvidence):
    item_type: Literal["failed_command"] = "failed_command"
    command_display: str


class FailedToolPatternReport(ReportPatternEvidence):
    item_type: Literal["failed_tool_result"] = "failed_tool_result"
    tool_name: str
    fingerprint: str


class ProblematicFilePatternReport(ReportPatternEvidence):
    item_type: Literal["problematic_file"] = "problematic_file"
    display_path: str


PatternItem = Annotated[
    FailedCommandPatternReport | FailedToolPatternReport | ProblematicFilePatternReport,
    Field(discriminator="item_type"),
]


class BoundedPatterns(ReportModel):
    status: Literal["available", "unavailable"]
    reason: str | None
    total: int
    displayed: int
    omitted: int
    items: list[PatternItem]


class ProjectContextReport(ReportModel):
    status: Literal["available", "unavailable"]
    reason: str | None
    scope_path: str | None
    scope_source: str | None
    window_start: datetime | None
    evidence_cutoff: datetime | None
    family_exclusions: dict[str, int]
    temporal_exclusions: dict[str, int]
    problematic_file_analysis_exclusions: dict[str, int]
    failed_commands: BoundedPatterns
    failed_tool_results: BoundedPatterns
    problematic_files: BoundedPatterns


class ReportStatement(ReportModel):
    code: str
    summary: str
    evidence_ids: list[str]


class SessionReport(ReportModel):
    schema_version: Literal[1] = 1
    session: ReportSession
    privacy: ReportPrivacy
    analysis: ReportAnalysis
    summary: ReportSummary
    scores: list[ReportScore]
    classifications: list[ReportClassification]
    evidence: dict[str, BoundedEvidence]
    ending: ReportEnding
    project_context: ProjectContextReport
    observations: list[ReportStatement]
    review_actions: list[ReportStatement]
    limitations: list[ReportStatement]

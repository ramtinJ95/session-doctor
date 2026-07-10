from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from .common import Confidence, SessionDoctorModel

NodeType = Literal[
    "session",
    "session_reference",
    "raw_event",
    "message",
    "tool_call",
    "tool_result",
    "command_run",
    "file_activity",
    "file",
    "failure_group",
    "message_feature",
    "session_feature",
    "classification",
    "parse_warning",
]
EdgeType = Literal[
    "contains",
    "parent_message",
    "derived_from",
    "has_tool_result",
    "runs_command",
    "targets_file",
    "member_of_failure_group",
    "repeats_request_of",
    "detected_in",
    "contributes_to_score",
    "supports_classification",
    "has_warning",
    "parent_session_reference",
    "child_session_reference",
]


class GraphModel(SessionDoctorModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphNodeBase(GraphModel):
    node_id: str
    node_type: NodeType
    label: str
    source_event_id: str | None = None
    timestamp: datetime | None = None


class SessionGraphNode(GraphNodeBase):
    node_type: Literal["session"] = "session"
    agent: str
    is_sidechain: bool
    started_at: datetime | None
    ended_at: datetime | None


class SessionReferenceGraphNode(GraphNodeBase):
    node_type: Literal["session_reference"] = "session_reference"
    referenced_session_id: str
    relationship: Literal["parent", "child"]
    agent: str | None
    is_sidechain: bool | None
    exists: bool


class RawEventGraphNode(GraphNodeBase):
    node_type: Literal["raw_event"] = "raw_event"
    record_index: int
    native_event_type: str | None


class MessageGraphNode(GraphNodeBase):
    node_type: Literal["message"] = "message"
    message_id: str
    role: str
    text_length: int
    content_block_types: list[str]


class ToolCallGraphNode(GraphNodeBase):
    node_type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    tool_name: str


class ToolResultGraphNode(GraphNodeBase):
    node_type: Literal["tool_result"] = "tool_result"
    tool_result_id: str
    tool_call_id: str | None
    is_error: bool | None
    output_length: int | None
    fingerprint: str | None


class CommandRunGraphNode(GraphNodeBase):
    node_type: Literal["command_run"] = "command_run"
    command_run_id: str
    command_display: str
    exit_code: int | None
    started_at: datetime | None
    ended_at: datetime | None


class FileActivityGraphNode(GraphNodeBase):
    node_type: Literal["file_activity"] = "file_activity"
    file_activity_id: str
    operation: str
    path_resolution: str


class FileGraphNode(GraphNodeBase):
    node_type: Literal["file"] = "file"
    display_path: str
    path_resolution: str


class FailureGroupGraphNode(GraphNodeBase):
    node_type: Literal["failure_group"] = "failure_group"
    group_type: str
    fingerprint: str
    occurrence_count: int


class MessageFeatureGraphNode(GraphNodeBase):
    node_type: Literal["message_feature"] = "message_feature"
    feature_id: str
    feature_name: str
    feature_value: str
    score: Confidence


class SessionFeatureGraphNode(GraphNodeBase):
    node_type: Literal["session_feature"] = "session_feature"
    feature_id: str
    feature_name: str
    feature_value: str
    score: Confidence


class ClassificationGraphNode(GraphNodeBase):
    node_type: Literal["classification"] = "classification"
    classification_id: str
    classification_label: str
    score: Confidence
    confidence: Confidence
    evidence_summary: str


class ParseWarningGraphNode(GraphNodeBase):
    node_type: Literal["parse_warning"] = "parse_warning"
    warning_id: str
    code: str
    severity: str
    record_index: int | None


GraphNode = Annotated[
    SessionGraphNode
    | SessionReferenceGraphNode
    | RawEventGraphNode
    | MessageGraphNode
    | ToolCallGraphNode
    | ToolResultGraphNode
    | CommandRunGraphNode
    | FileActivityGraphNode
    | FileGraphNode
    | FailureGroupGraphNode
    | MessageFeatureGraphNode
    | SessionFeatureGraphNode
    | ClassificationGraphNode
    | ParseWarningGraphNode,
    Field(discriminator="node_type"),
]


class GraphEdge(GraphModel):
    edge_id: str
    edge_type: EdgeType
    source_node_id: str
    target_node_id: str
    confidence: Confidence = 1.0
    source_event_id: str | None = None


class GraphAnalysis(GraphModel):
    status: Literal["current", "stale", "missing"]
    current_analyzer_version: str
    observed_analyzer_version: str | None
    analysis_run_id: str | None
    action: str | None


class GraphPrivacy(GraphModel):
    message_text_included: Literal[False] = False


class GraphCounts(GraphModel):
    nodes: int
    edges: int
    nodes_by_type: dict[str, int]
    edges_by_type: dict[str, int]


class GraphExcluded(GraphModel):
    rows_by_type: dict[str, int]
    unresolved_references: dict[str, int]


class GraphReport(GraphModel):
    schema_version: Literal[1] = 1
    session_id: str
    analysis: GraphAnalysis
    privacy: GraphPrivacy = Field(default_factory=GraphPrivacy)
    directed: Literal[True] = True
    multigraph: Literal[True] = True
    counts: GraphCounts
    excluded: GraphExcluded
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @model_validator(mode="after")
    def validate_graph(self) -> GraphReport:
        node_ids = [node.node_id for node in self.nodes]
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("graph node IDs must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("graph edge IDs must be unique")
        known_nodes = set(node_ids)
        if any(
            edge.source_node_id not in known_nodes or edge.target_node_id not in known_nodes
            for edge in self.edges
        ):
            raise ValueError("graph edges must have existing endpoints")
        if self.counts.nodes != len(self.nodes) or self.counts.edges != len(self.edges):
            raise ValueError("graph totals must match projected rows")
        return self

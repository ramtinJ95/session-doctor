from __future__ import annotations

from collections import Counter
from datetime import datetime
from math import isfinite

from session_doctor.diagnostic_models import DiagnosticSnapshot
from session_doctor.ids import stable_id
from session_doctor.privacy import (
    display_file_path,
    public_fingerprint,
    redact_command_for_display,
)
from session_doctor.schemas.graph import (
    ClassificationGraphNode,
    CommandRunGraphNode,
    EdgeType,
    FailureGroupGraphNode,
    FileActivityGraphNode,
    FileGraphNode,
    GraphAnalysis,
    GraphCounts,
    GraphEdge,
    GraphExcluded,
    GraphNode,
    GraphReport,
    MessageFeatureGraphNode,
    MessageGraphNode,
    ParseWarningGraphNode,
    RawEventGraphNode,
    SessionFeatureGraphNode,
    SessionGraphNode,
    SessionReferenceGraphNode,
    ToolCallGraphNode,
    ToolResultGraphNode,
)

NODE_TYPE_ORDER = (
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
)
EDGE_TYPE_ORDER = (
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
)
FAILURE_GROUP_TYPES = {
    "command_stderr_hash",
    "command_stdout_hash",
    "failed_command_identity",
    "tool_output_hash",
}
SCORE_NAMES = {
    "friction_score",
    "stuckness_score",
    "prompt_clarity_risk",
    "agent_fit_risk",
    "project_complexity_signal",
}


class GraphBuilder:
    def __init__(self, snapshot: DiagnosticSnapshot) -> None:
        self.snapshot = snapshot
        self.session_id = snapshot.normalized.session.session_id
        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []
        self.edge_ids: set[str] = set()
        self.node_ids: dict[tuple[str, str], str] = {}
        self.unresolved: Counter[str] = Counter()
        self.excluded: Counter[str] = Counter({"model_usage": len(snapshot.normalized.model_usage)})

    def build(self) -> GraphReport:
        self.add_normalized_nodes()
        self.add_topology_nodes()
        self.add_analysis_nodes()
        self.add_failure_groups()
        self.add_normalized_relations()
        self.add_analysis_relations()
        self.add_warning_relations()
        self.add_contains_edges()
        self.nodes.sort(key=self.node_sort_key)
        self.edges.sort(key=self.edge_sort_key)
        node_counts = Counter(node.node_type for node in self.nodes)
        edge_counts = Counter(edge.edge_type for edge in self.edges)
        return GraphReport(
            session_id=self.session_id,
            analysis=GraphAnalysis(
                status=self.snapshot.analysis.compatibility.value,
                current_analyzer_version=self.snapshot.analysis.current_analyzer_version,
                observed_analyzer_version=self.snapshot.analysis.observed_analyzer_version,
                analysis_run_id=self.snapshot.analysis.analysis_run_id,
                action=self.snapshot.analysis.action,
            ),
            counts=GraphCounts(
                nodes=len(self.nodes),
                edges=len(self.edges),
                nodes_by_type={name: node_counts[name] for name in NODE_TYPE_ORDER},
                edges_by_type={name: edge_counts[name] for name in EDGE_TYPE_ORDER},
            ),
            excluded=GraphExcluded(
                rows_by_type=dict(sorted(self.excluded.items())),
                unresolved_references={name: self.unresolved[name] for name in EDGE_TYPE_ORDER},
            ),
            nodes=self.nodes,
            edges=self.edges,
        )

    def add_normalized_nodes(self) -> None:
        session = self.snapshot.normalized.session
        self.add_node(
            "session",
            session.session_id,
            SessionGraphNode(
                node_id=self.node_id("session", session.session_id),
                label="session",
                timestamp=session.started_at,
                agent=session.agent_name.value,
                is_sidechain=session.is_sidechain,
                started_at=session.started_at,
                ended_at=session.ended_at,
            ),
        )
        for event in self.snapshot.normalized.raw_events:
            self.add_node(
                "raw_event",
                event.event_id,
                RawEventGraphNode(
                    node_id=self.node_id("raw_event", event.event_id),
                    label=event.native_event_type or "raw_event",
                    source_event_id=event.event_id,
                    timestamp=event.timestamp,
                    record_index=event.record_index,
                    native_event_type=event.native_event_type,
                ),
            )
        for message in self.snapshot.normalized.messages:
            self.add_node(
                "message",
                message.message_id,
                MessageGraphNode(
                    node_id=self.node_id("message", message.message_id),
                    label=f"message:{message.role.value}",
                    source_event_id=message.source_event_id,
                    timestamp=message.timestamp,
                    message_id=message.message_id,
                    role=message.role.value,
                    text_length=message.text_length,
                    content_block_types=sorted(message.content_block_types),
                ),
            )
        for call in self.snapshot.normalized.tool_calls:
            self.add_node(
                "tool_call",
                call.tool_call_id,
                ToolCallGraphNode(
                    node_id=self.node_id("tool_call", call.tool_call_id),
                    label=f"tool:{call.name}",
                    source_event_id=call.source_event_id,
                    timestamp=call.timestamp,
                    tool_call_id=call.tool_call_id,
                    tool_name=call.name,
                ),
            )
        for result in self.snapshot.normalized.tool_results:
            self.add_node(
                "tool_result",
                result.tool_result_id,
                ToolResultGraphNode(
                    node_id=self.node_id("tool_result", result.tool_result_id),
                    label="tool_result:error" if result.is_error else "tool_result",
                    source_event_id=result.source_event_id,
                    timestamp=result.timestamp,
                    tool_result_id=result.tool_result_id,
                    tool_call_id=result.tool_call_id,
                    is_error=result.is_error,
                    output_length=result.output_length,
                    fingerprint=(
                        public_fingerprint("tool-result", result.output_hash)
                        if result.is_error and result.output_hash
                        else None
                    ),
                ),
            )
        for command in self.snapshot.normalized.command_runs:
            self.add_node(
                "command_run",
                command.command_run_id,
                CommandRunGraphNode(
                    node_id=self.node_id("command_run", command.command_run_id),
                    label="command",
                    source_event_id=command.source_event_id,
                    timestamp=command.ended_at or command.started_at,
                    command_run_id=command.command_run_id,
                    command_display=redact_command_for_display(command.command_display),
                    exit_code=command.exit_code,
                    started_at=command.started_at,
                    ended_at=command.ended_at,
                ),
            )
        self.add_file_nodes()
        for warning in self.snapshot.normalized.parse_warnings:
            code = warning.metadata.get("code")
            safe_code = code if isinstance(code, str) else "unknown"
            self.add_node(
                "parse_warning",
                warning.warning_id,
                ParseWarningGraphNode(
                    node_id=self.node_id("parse_warning", warning.warning_id),
                    label=f"warning:{safe_code}",
                    warning_id=warning.warning_id,
                    code=safe_code,
                    severity=warning.severity,
                    record_index=warning.record_index,
                ),
            )

    def add_file_nodes(self) -> None:
        for activity in self.snapshot.normalized.file_activities:
            self.add_node(
                "file_activity",
                activity.file_activity_id,
                FileActivityGraphNode(
                    node_id=self.node_id("file_activity", activity.file_activity_id),
                    label=f"file_activity:{activity.operation}",
                    source_event_id=activity.source_event_id,
                    timestamp=activity.timestamp,
                    file_activity_id=activity.file_activity_id,
                    operation=activity.operation,
                    path_resolution=activity.path_resolution,
                ),
            )
            file_key = self.file_key(activity)
            if ("file", file_key) not in self.node_ids:
                display_path = display_file_path(
                    project_relative_path=activity.project_relative_path,
                    normalized_path=activity.normalized_path,
                    canonical_path=activity.canonical_path,
                )
                self.add_node(
                    "file",
                    file_key,
                    FileGraphNode(
                        node_id=self.node_id("file", file_key),
                        label=display_path,
                        display_path=display_path,
                        path_resolution=activity.path_resolution,
                    ),
                )

    def add_topology_nodes(self) -> None:
        for reference in self.snapshot.topology_references:
            key = f"{reference.relationship}:{reference.session_id}"
            self.add_node(
                "session_reference",
                key,
                SessionReferenceGraphNode(
                    node_id=self.node_id("session_reference", key),
                    label=f"{reference.relationship}_session",
                    referenced_session_id=reference.session_id,
                    relationship=reference.relationship,
                    agent=reference.agent_name.value if reference.agent_name else None,
                    is_sidechain=reference.is_sidechain,
                    exists=reference.exists,
                ),
            )
            relationship_edge: EdgeType = (
                "parent_session_reference"
                if reference.relationship == "parent"
                else "child_session_reference"
            )
            self.add_edge(
                relationship_edge,
                self.require_node("session", self.session_id),
                self.require_node("session_reference", key),
            )

    def add_analysis_nodes(self) -> None:
        for feature in self.snapshot.analysis.message_features:
            self.add_node(
                "message_feature",
                feature.message_feature_id,
                MessageFeatureGraphNode(
                    node_id=self.node_id("message_feature", feature.message_feature_id),
                    label=f"message_feature:{feature.feature_name}",
                    source_event_id=feature.source_event_id,
                    feature_id=feature.message_feature_id,
                    feature_name=feature.feature_name,
                    feature_value=feature.feature_value,
                    score=feature.score,
                ),
            )
        for feature in self.snapshot.analysis.session_features:
            self.add_node(
                "session_feature",
                feature.session_feature_id,
                SessionFeatureGraphNode(
                    node_id=self.node_id("session_feature", feature.session_feature_id),
                    label=f"session_feature:{feature.feature_name}",
                    feature_id=feature.session_feature_id,
                    feature_name=feature.feature_name,
                    feature_value=feature.feature_value,
                    score=feature.score,
                ),
            )
        for classification in self.snapshot.analysis.classifications:
            self.add_node(
                "classification",
                classification.session_classification_id,
                ClassificationGraphNode(
                    node_id=self.node_id(
                        "classification", classification.session_classification_id
                    ),
                    label=f"classification:{classification.label}",
                    classification_id=classification.session_classification_id,
                    classification_label=classification.label,
                    score=classification.score,
                    confidence=classification.confidence,
                    evidence_summary=classification.evidence_summary,
                ),
            )

    def add_failure_groups(self) -> None:
        repeated = next(
            (
                feature
                for feature in self.snapshot.analysis.session_features
                if feature.feature_name == "repeated_failure_count"
            ),
            None,
        )
        groups = repeated.evidence.get("groups") if repeated else None
        if not isinstance(groups, list):
            return
        for group in groups:
            if not isinstance(group, dict):
                self.excluded["failure_group"] += 1
                continue
            group_type = group.get("group_type")
            private_key = group.get("key")
            record_ids = self.string_list(group.get("record_ids"))
            if (
                not isinstance(group_type, str)
                or group_type not in FAILURE_GROUP_TYPES
                or not isinstance(private_key, str)
                or len(record_ids) < 2
            ):
                self.excluded["failure_group"] += 1
                continue
            known_type = "tool_result" if group_type == "tool_output_hash" else "command_run"
            known_records = [
                record_id for record_id in record_ids if (known_type, record_id) in self.node_ids
            ]
            missing = len(record_ids) - len(known_records)
            self.unresolved["member_of_failure_group"] += missing
            if len(known_records) < 2:
                self.excluded["failure_group"] += 1
                continue
            fingerprint = public_fingerprint(group_type, private_key)
            group_key = f"{group_type}:{fingerprint}"
            self.add_node(
                "failure_group",
                group_key,
                FailureGroupGraphNode(
                    node_id=self.node_id("failure_group", group_key),
                    label=f"failure_group:{group_type}",
                    group_type=group_type,
                    fingerprint=fingerprint,
                    occurrence_count=len(known_records),
                ),
            )
            group_node = self.require_node("failure_group", group_key)
            for record_id in known_records:
                self.add_edge(
                    "member_of_failure_group",
                    self.require_node(known_type, record_id),
                    group_node,
                )
            for event_id in self.string_list(group.get("source_event_ids")):
                self.add_provenance_edge(group_node, event_id)

    def add_normalized_relations(self) -> None:
        for message in self.snapshot.normalized.messages:
            node = self.require_node("message", message.message_id)
            self.add_optional_provenance(node, message.source_event_id)
            if message.parent_message_id is not None:
                parent = self.node_ids.get(("message", message.parent_message_id))
                if parent:
                    self.add_edge("parent_message", node, parent, message.source_event_id)
                else:
                    self.unresolved["parent_message"] += 1
        for call in self.snapshot.normalized.tool_calls:
            self.add_optional_provenance(
                self.require_node("tool_call", call.tool_call_id), call.source_event_id
            )
        for result in self.snapshot.normalized.tool_results:
            node = self.require_node("tool_result", result.tool_result_id)
            self.add_optional_provenance(node, result.source_event_id)
            if result.tool_call_id is not None:
                call = self.node_ids.get(("tool_call", result.tool_call_id))
                if call:
                    self.add_edge("has_tool_result", call, node, result.source_event_id)
                else:
                    self.unresolved["has_tool_result"] += 1
        for command in self.snapshot.normalized.command_runs:
            node = self.require_node("command_run", command.command_run_id)
            self.add_optional_provenance(node, command.source_event_id)
            if command.tool_call_id is not None:
                call = self.node_ids.get(("tool_call", command.tool_call_id))
                if call:
                    self.add_edge("runs_command", call, node, command.source_event_id)
                else:
                    self.unresolved["runs_command"] += 1
        for activity in self.snapshot.normalized.file_activities:
            node = self.require_node("file_activity", activity.file_activity_id)
            self.add_optional_provenance(node, activity.source_event_id)
            self.add_edge(
                "targets_file",
                node,
                self.require_node("file", self.file_key(activity)),
                activity.source_event_id,
            )

    def add_analysis_relations(self) -> None:
        features_by_name: dict[str, list[str]] = {}
        for feature in self.snapshot.analysis.session_features:
            features_by_name.setdefault(feature.feature_name, []).append(feature.session_feature_id)
        for feature in self.snapshot.analysis.message_features:
            feature_node = self.require_node("message_feature", feature.message_feature_id)
            self.add_optional_provenance(feature_node, feature.source_event_id)
            message_node = self.node_ids.get(("message", feature.message_id))
            if message_node:
                self.add_edge("detected_in", feature_node, message_node, feature.source_event_id)
            else:
                self.unresolved["detected_in"] += 1
            if feature.feature_name == "repeat_request_similarity":
                matched_id = feature.evidence.get("matched_message_id")
                similarity = self.confidence(feature.evidence.get("similarity_score"))
                repeated_node = self.node_ids.get(("message", feature.message_id))
                matched_node = (
                    self.node_ids.get(("message", matched_id))
                    if isinstance(matched_id, str)
                    else None
                )
                if repeated_node and matched_node and similarity is not None:
                    self.add_edge(
                        "repeats_request_of",
                        repeated_node,
                        matched_node,
                        feature.source_event_id,
                        similarity,
                    )
                else:
                    self.unresolved["repeats_request_of"] += 1
        for feature in self.snapshot.analysis.session_features:
            feature_node = self.require_node("session_feature", feature.session_feature_id)
            for event_id in self.string_list(feature.evidence.get("source_event_ids")):
                self.add_provenance_edge(feature_node, event_id)
            if feature.feature_name not in SCORE_NAMES:
                continue
            contributing = self.string_list(feature.evidence.get("contributing_features"))
            for component_name in contributing:
                candidates = features_by_name.get(component_name, [])
                if len(candidates) == 1:
                    self.add_edge(
                        "contributes_to_score",
                        self.require_node("session_feature", candidates[0]),
                        feature_node,
                    )
                else:
                    self.unresolved["contributes_to_score"] += 1
        for classification in self.snapshot.analysis.classifications:
            classification_node = self.require_node(
                "classification", classification.session_classification_id
            )
            for event_id in classification.evidence_event_ids:
                raw_node = self.node_ids.get(("raw_event", event_id))
                if raw_node:
                    self.add_edge("derived_from", classification_node, raw_node, event_id)
                    self.add_edge(
                        "supports_classification", raw_node, classification_node, event_id
                    )
                else:
                    self.unresolved["derived_from"] += 1
                    self.unresolved["supports_classification"] += 1

    def add_warning_relations(self) -> None:
        session_node = self.require_node("session", self.session_id)
        for warning in self.snapshot.normalized.parse_warnings:
            warning_node = self.require_node("parse_warning", warning.warning_id)
            events = (
                self.snapshot.indexes.raw_events_by_record_index.get(warning.record_index, ())
                if warning.record_index is not None
                else ()
            )
            if len(events) == 1:
                event = events[0]
                self.add_edge(
                    "has_warning",
                    self.require_node("raw_event", event.event_id),
                    warning_node,
                    event.event_id,
                )
            else:
                self.add_edge("has_warning", session_node, warning_node)
                self.unresolved["has_warning"] += 1

    def add_contains_edges(self) -> None:
        session_node = self.require_node("session", self.session_id)
        for node in self.nodes:
            if node.node_type in {"session", "session_reference"}:
                continue
            self.add_edge(
                "contains",
                session_node,
                node.node_id,
                node.source_event_id,
            )

    def add_optional_provenance(self, node_id: str, event_id: str | None) -> None:
        if event_id is not None:
            self.add_provenance_edge(node_id, event_id)

    def add_provenance_edge(self, node_id: str, event_id: str) -> None:
        raw_node = self.node_ids.get(("raw_event", event_id))
        if raw_node:
            self.add_edge("derived_from", node_id, raw_node, event_id)
        else:
            self.unresolved["derived_from"] += 1

    def add_node(self, node_type: str, semantic_id: str, node: GraphNode) -> None:
        key = (node_type, semantic_id)
        if key in self.node_ids:
            raise ValueError(f"duplicate semantic graph node: {node_type}")
        self.node_ids[key] = node.node_id
        self.nodes.append(node)

    def add_edge(
        self,
        edge_type: EdgeType,
        source_node_id: str,
        target_node_id: str,
        source_event_id: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        edge_id = stable_id(
            "graph-edge",
            self.session_id,
            edge_type,
            source_node_id,
            target_node_id,
            source_event_id,
        )
        if edge_id in self.edge_ids:
            return
        self.edge_ids.add(edge_id)
        self.edges.append(
            GraphEdge(
                edge_id=edge_id,
                edge_type=edge_type,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                confidence=confidence,
                source_event_id=source_event_id,
            )
        )

    def node_id(self, node_type: str, semantic_id: str) -> str:
        return stable_id("graph-node", self.session_id, node_type, semantic_id)

    def require_node(self, node_type: str, semantic_id: str) -> str:
        return self.node_ids[(node_type, semantic_id)]

    def file_key(self, activity) -> str:
        if activity.canonical_path:
            return f"canonical:{activity.canonical_path}"
        if activity.project_relative_path:
            return f"project_relative:{activity.project_relative_path}"
        return f"session_local:{activity.file_activity_id}"

    def node_sort_key(self, node: GraphNode) -> tuple[int, bool, int, bool, datetime, str]:
        event = self.snapshot.indexes.raw_events_by_id.get(node.source_event_id or "")
        return (
            NODE_TYPE_ORDER.index(node.node_type),
            event is None,
            event.record_index if event else 0,
            node.timestamp is None,
            node.timestamp or datetime.min,
            node.node_id,
        )

    def edge_sort_key(self, edge: GraphEdge) -> tuple[int, str, str, str]:
        return (
            EDGE_TYPE_ORDER.index(edge.edge_type),
            edge.source_node_id,
            edge.target_node_id,
            edge.edge_id,
        )

    @staticmethod
    def string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def confidence(value: object) -> float | None:
        if not isinstance(value, int | float) or isinstance(value, bool):
            return None
        result = float(value)
        return result if isfinite(result) and 0.0 <= result <= 1.0 else None


def project_graph(snapshot: DiagnosticSnapshot) -> GraphReport:
    return GraphBuilder(snapshot).build()

from __future__ import annotations

from pydantic import Field

from .common import Confidence, Metadata, SessionDoctorModel


class GraphNode(SessionDoctorModel):
    node_id: str
    session_id: str
    node_type: str
    label: str
    source_event_id: str | None = None
    metadata: Metadata = Field(default_factory=dict)


class GraphEdge(SessionDoctorModel):
    edge_id: str
    session_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str
    confidence: Confidence = 1.0
    source_event_id: str | None = None
    metadata: Metadata = Field(default_factory=dict)

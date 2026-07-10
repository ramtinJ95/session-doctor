from __future__ import annotations

from session_doctor.schemas.graph import GraphReport


def graph_payload(graph: GraphReport) -> dict[str, object]:
    return graph.model_dump(mode="json")

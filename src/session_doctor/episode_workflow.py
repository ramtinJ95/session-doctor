from __future__ import annotations

from pathlib import Path

from session_doctor.adapters import built_in_adapters
from session_doctor.schemas import AgentName, EpisodeAnalysis
from session_doctor.segmentation import segment_session
from session_doctor.store import DuckDBStore
from session_doctor.store.connection import read_connection


class EpisodeAnalysisUnavailable(ValueError):
    pass


def analyze_session_episodes(
    store: DuckDBStore,
    session_id: str,
    database_path: Path,
) -> EpisodeAnalysis:
    with read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT sources.snapshot_bundle_id, sessions.agent_name
            FROM sessions
            JOIN session_sources AS sources USING (source_id)
            WHERE sessions.session_id = ?
            """,
            [session_id],
        ).fetchone()
    if row is None:
        raise EpisodeAnalysisUnavailable("session has no normalized v2 input")
    snapshot_bundle_id = str(row[0])
    agent_name = AgentName(str(row[1]))
    adapter = next(item for item in built_in_adapters() if item.name is agent_name)
    coverage = store.normalization_coverage(
        snapshot_bundle_id,
        adapter_name=adapter.name.value,
        adapter_version=adapter.version,
        capability_declarations=adapter.capabilities,
    )
    if coverage.selected_normalization_run_id is None:
        raise EpisodeAnalysisUnavailable("normalization input is unavailable")
    stored = store.load_normalization(
        coverage.selected_normalization_run_id,
        snapshot_bundle_id,
    )
    if stored is None:
        raise EpisodeAnalysisUnavailable("normalization input is unavailable")
    lifecycle = store.lifecycle_for_bundle(stored.run.snapshot_bundle_id)
    if lifecycle is None:
        raise EpisodeAnalysisUnavailable("lifecycle observation is unavailable")
    return segment_session(stored.bundle, lifecycle)

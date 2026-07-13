from __future__ import annotations

from pathlib import Path

from session_doctor.schemas import EpisodeAnalysis
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
            SELECT entities.normalization_run_id
            FROM normalized_entities AS entities
            JOIN normalization_runs AS runs USING (normalization_run_id)
            WHERE entities.entity_kind = 'session'
              AND json_extract_string(entities.payload_json, '$.session_id') = ?
            ORDER BY runs.created_at DESC, entities.normalization_run_id DESC
            LIMIT 1
            """,
            [session_id],
        ).fetchone()
    if row is None:
        raise EpisodeAnalysisUnavailable("session has no normalized v2 input")
    stored = store.load_normalization(str(row[0]))
    if stored is None:
        raise EpisodeAnalysisUnavailable("normalization input is unavailable")
    lifecycle = store.lifecycle_for_bundle(stored.run.snapshot_bundle_id)
    if lifecycle is None:
        raise EpisodeAnalysisUnavailable("lifecycle observation is unavailable")
    return segment_session(stored.bundle, lifecycle)

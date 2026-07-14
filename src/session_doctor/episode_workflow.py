from __future__ import annotations

from pathlib import Path

from session_doctor.adapters import built_in_adapters
from session_doctor.schemas import EpisodeAnalysisPayload, Session
from session_doctor.store import DuckDBStore
from session_doctor.store.connection import read_connection, transaction, write_connection
from session_doctor.store.episode_analysis import (
    EpisodePersistenceConflict,
    load_episode_analysis,
    persist_requested_episode_analysis,
    select_exact_episode_input,
)


class EpisodeAnalysisUnavailable(ValueError):
    pass


def analyze_session_episodes(
    _store: DuckDBStore,
    session_id: str,
    database_path: Path,
    *,
    snapshot_id: str | None = None,
    projection_id: str | None = None,
) -> EpisodeAnalysisPayload:
    if snapshot_id is not None and projection_id is not None:
        raise EpisodeAnalysisUnavailable("snapshot and projection options are mutually exclusive")
    if projection_id is not None:
        try:
            with read_connection(database_path) as connection:
                connection.execute("BEGIN TRANSACTION")
                payload = load_episode_analysis(connection, projection_id, session_id)
                connection.execute("COMMIT")
                return payload
        except (ValueError, EpisodePersistenceConflict) as exc:
            raise EpisodeAnalysisUnavailable(str(exc)) from exc

    try:
        with write_connection(database_path) as connection, transaction(connection):
            adapter = adapter_for_immutable_session(connection, session_id, snapshot_id)
            exact = select_exact_episode_input(
                connection,
                session_id,
                adapter,
                snapshot_id=snapshot_id,
            )
            return persist_requested_episode_analysis(connection, exact)
    except (ValueError, EpisodePersistenceConflict) as exc:
        raise EpisodeAnalysisUnavailable(str(exc)) from exc


def adapter_for_immutable_session(connection, session_id: str, snapshot_id: str | None):
    rows = connection.execute(
        """
        SELECT entities.payload_json, snapshots.snapshot_id
        FROM normalized_entities AS entities
        JOIN normalization_run_bundles AS links USING (normalization_run_id)
        JOIN snapshot_bundles AS bundles USING (snapshot_bundle_id)
        JOIN source_snapshots AS snapshots
            ON snapshots.snapshot_id = bundles.primary_snapshot_id
        WHERE entities.entity_kind = 'session'
        """
    ).fetchall()
    agent_names = {
        session.agent_name
        for payload, candidate_snapshot_id in rows
        if (session := Session.model_validate_json(str(payload))).session_id == session_id
        and (snapshot_id is None or str(candidate_snapshot_id) == snapshot_id)
    }
    if not agent_names:
        raise EpisodeAnalysisUnavailable("session has no normalized v2 input")
    if len(agent_names) != 1:
        raise EpisodePersistenceConflict("session resolves to multiple immutable agent identities")
    agent_name = next(iter(agent_names))
    adapter = next((item for item in built_in_adapters() if item.name is agent_name), None)
    if adapter is None:
        raise EpisodeAnalysisUnavailable("session adapter is unavailable")
    return adapter

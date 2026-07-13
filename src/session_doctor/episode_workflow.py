from __future__ import annotations

from pathlib import Path

from session_doctor.adapters import built_in_adapters
from session_doctor.schemas import AgentName, EpisodeAnalysis
from session_doctor.segmentation import segment_session
from session_doctor.store import DuckDBStore
from session_doctor.store.connection import read_connection
from session_doctor.store.json_values import parse_metadata
from session_doctor.store.lifecycle import LifecycleObservation
from session_doctor.store.normalization_runs import (
    NORMALIZATION_CONFIGURATION_HASH,
    NORMALIZATION_VERSION,
    load_normalization_from_connection,
    normalization_configuration_hash,
    parser_version_key,
    versions_compatible,
)


class EpisodeAnalysisUnavailable(ValueError):
    pass


def analyze_session_episodes(
    _store: DuckDBStore,
    session_id: str,
    database_path: Path,
) -> EpisodeAnalysis:
    with read_connection(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
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
        logical_source = connection.execute(
            """
            SELECT snapshots.logical_source_id
            FROM snapshot_bundles AS bundles
            JOIN source_snapshots AS snapshots
                ON snapshots.snapshot_id = bundles.primary_snapshot_id
            WHERE bundles.snapshot_bundle_id = ?
            """,
            [snapshot_bundle_id],
        ).fetchone()
        latest_snapshot = (
            connection.execute(
                """
                SELECT snapshots.snapshot_id, bundles.snapshot_bundle_id
                FROM source_snapshots AS snapshots
                LEFT JOIN snapshot_bundles AS bundles
                    ON bundles.primary_snapshot_id = snapshots.snapshot_id
                WHERE snapshots.logical_source_id = ?
                ORDER BY snapshots.capture_sequence DESC
                LIMIT 1
                """,
                [str(logical_source[0])],
            ).fetchone()
            if logical_source is not None
            else None
        )
        if (
            latest_snapshot is None
            or latest_snapshot[1] is None
            or str(latest_snapshot[1]) != snapshot_bundle_id
        ):
            raise EpisodeAnalysisUnavailable("latest capture has no current normalized projection")

        agent_name = AgentName(str(row[1]))
        adapter = next(item for item in built_in_adapters() if item.name is agent_name)
        expected_configuration = normalization_configuration_hash(
            NORMALIZATION_CONFIGURATION_HASH,
            adapter.capabilities,
        )
        run_rows = connection.execute(
            """
            SELECT runs.normalization_run_id, runs.adapter_name,
                runs.adapter_version, runs.normalization_version,
                runs.configuration_hash
            FROM normalization_run_bundles AS links
            JOIN normalization_runs AS runs USING (normalization_run_id)
            WHERE links.snapshot_bundle_id = ?
            """,
            [snapshot_bundle_id],
        ).fetchall()
        ordered_runs = sorted(
            run_rows,
            key=lambda run: (parser_version_key(str(run[2])) or (-1,), str(run[0])),
            reverse=True,
        )
        selected_run_id = next(
            (
                str(run[0])
                for run in ordered_runs
                if run[1:]
                == (
                    adapter.name.value,
                    adapter.version,
                    NORMALIZATION_VERSION,
                    expected_configuration,
                )
            ),
            None,
        )
        if selected_run_id is None:
            current_version = parser_version_key(adapter.version)
            selected_run_id = next(
                (
                    str(run[0])
                    for run in ordered_runs
                    if str(run[1]) == adapter.name.value
                    and str(run[3]) == NORMALIZATION_VERSION
                    and str(run[4]) == expected_configuration
                    and versions_compatible(
                        parser_version_key(str(run[2])),
                        current_version,
                        str(run[2]),
                        adapter.version,
                    )
                ),
                None,
            )
        if selected_run_id is None:
            raise EpisodeAnalysisUnavailable("normalization input is unavailable")
        stored = load_normalization_from_connection(
            connection,
            selected_run_id,
            snapshot_bundle_id,
        )
        lifecycle_row = connection.execute(
            """
            SELECT lifecycle_observation_id, snapshot_bundle_id, state,
                observed_at, evidence_json
            FROM lifecycle_observations
            WHERE snapshot_bundle_id = ?
            """,
            [snapshot_bundle_id],
        ).fetchone()
        if stored is None or lifecycle_row is None:
            raise EpisodeAnalysisUnavailable("lifecycle or normalization input is unavailable")
        lifecycle = LifecycleObservation(
            lifecycle_observation_id=str(lifecycle_row[0]),
            snapshot_bundle_id=str(lifecycle_row[1]),
            state=str(lifecycle_row[2]),
            observed_at=lifecycle_row[3],
            evidence=parse_metadata(lifecycle_row[4]),
        )
        analysis = segment_session(stored.bundle, lifecycle)
        connection.execute("COMMIT")
        return analysis

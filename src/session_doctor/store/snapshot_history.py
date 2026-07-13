from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .connection import read_connection, transaction, write_connection
from .lifecycle import LIFECYCLE_STATES
from .writers import delete_source_records


@dataclass(frozen=True)
class SnapshotSummary:
    snapshot_id: str
    snapshot_bundle_id: str
    source_id: str
    agent_name: str
    source_path: str
    capture_sequence: int
    captured_at: object
    lifecycle_state: str
    capture_status: str
    byte_length: int
    is_latest: bool


@dataclass(frozen=True)
class PruneResult:
    snapshot_id: str
    deleted_bundle_count: int
    deleted_blob_count: int
    dependent_source_ids: tuple[str, ...]
    dependent_session_ids: tuple[str, ...]
    dependent_analysis_run_ids: tuple[str, ...]
    derived_row_counts: dict[str, int]
    forced: bool
    checkpoint_completed: bool


@dataclass(frozen=True)
class PruneDependencies:
    bundle_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    session_ids: tuple[str, ...]
    analysis_run_ids: tuple[str, ...]
    derived_row_counts: dict[str, int]


class SnapshotPruneBlocked(RuntimeError):
    def __init__(self, snapshot_id: str, dependencies: PruneDependencies) -> None:
        self.snapshot_id = snapshot_id
        self.dependencies = dependencies
        self.dependent_source_ids = dependencies.source_ids
        super().__init__(
            f"snapshot {snapshot_id} has dependent normalized sources: "
            f"{', '.join(dependencies.source_ids)}"
        )


def snapshot_dependencies(database_path: Path, snapshot_id: str) -> PruneDependencies:
    with read_connection(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM source_snapshots WHERE snapshot_id = ?", [snapshot_id]
        ).fetchone()
        if exists is None:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        bundle_rows = connection.execute(
            """
            SELECT snapshot_bundle_id FROM snapshot_bundles
            WHERE primary_snapshot_id = ? ORDER BY snapshot_bundle_id
            """,
            [snapshot_id],
        ).fetchall()
        if not bundle_rows:
            raise ValueError(
                "Only primary snapshots can be pruned; prune the owning bundle's primary"
            )
        bundle_ids = tuple(str(row[0]) for row in bundle_rows)
        source_rows = connection.execute(
            """
            SELECT source_id FROM session_sources
            WHERE snapshot_bundle_id IN (SELECT unnest(?))
            ORDER BY source_id
            """,
            [list(bundle_ids)],
        ).fetchall()
        source_ids = tuple(str(row[0]) for row in source_rows)
        session_rows = (
            connection.execute(
                "SELECT session_id FROM sessions WHERE source_id IN (SELECT unnest(?)) "
                "ORDER BY session_id",
                [list(source_ids)],
            ).fetchall()
            if source_ids
            else []
        )
        session_ids = tuple(str(row[0]) for row in session_rows)
        analysis_rows = (
            connection.execute(
                "SELECT analysis_run_id FROM analysis_runs "
                "WHERE session_id IN (SELECT unnest(?)) ORDER BY analysis_run_id",
                [list(session_ids)],
            ).fetchall()
            if session_ids
            else []
        )
        analysis_run_ids = tuple(str(row[0]) for row in analysis_rows)
        derived_row_counts: dict[str, int] = {
            "session_sources": len(source_ids),
            "sessions": len(session_ids),
        }
        for table_name in ("raw_events", "parse_warnings"):
            if not source_ids:
                derived_row_counts[table_name] = 0
                continue
            count_row = connection.execute(
                f"SELECT count(*) FROM {table_name} WHERE source_id IN (SELECT unnest(?))",
                [list(source_ids)],
            ).fetchone()
            derived_row_counts[table_name] = int(count_row[0]) if count_row else 0
        for table_name in (
            "messages",
            "tool_calls",
            "tool_results",
            "command_runs",
            "file_activities",
            "model_usage",
            "analysis_runs",
            "message_features",
            "session_features",
            "session_classifications",
        ):
            if not session_ids:
                derived_row_counts[table_name] = 0
                continue
            count_row = connection.execute(
                f"SELECT count(*) FROM {table_name} WHERE session_id IN (SELECT unnest(?))",
                [list(session_ids)],
            ).fetchone()
            derived_row_counts[table_name] = int(count_row[0]) if count_row else 0
    return PruneDependencies(
        bundle_ids=bundle_ids,
        source_ids=source_ids,
        session_ids=session_ids,
        analysis_run_ids=analysis_run_ids,
        derived_row_counts=derived_row_counts,
    )


def list_snapshots(
    database_path: Path,
    *,
    agent_name: str | None = None,
    lifecycle_state: str | None = None,
) -> tuple[SnapshotSummary, ...]:
    if lifecycle_state is not None and lifecycle_state not in LIFECYCLE_STATES:
        raise ValueError(f"Unknown lifecycle state: {lifecycle_state}")
    clauses: list[str] = []
    params: list[object] = []
    if agent_name is not None:
        clauses.append("s.agent_name = ?")
        params.append(agent_name)
    if lifecycle_state is not None:
        clauses.append("l.state = ?")
        params.append(lifecycle_state)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            WITH candidate AS (
                SELECT s.snapshot_id, b.snapshot_bundle_id, s.source_id,
                    s.agent_name, s.source_path, s.logical_source_id,
                    s.capture_sequence, s.captured_at, l.state,
                    c.capture_status, blobs.original_byte_length
                FROM source_snapshots AS s
                JOIN snapshot_bundles AS b ON b.primary_snapshot_id = s.snapshot_id
                JOIN bundle_capture_metadata AS c USING (snapshot_bundle_id)
                JOIN lifecycle_observations AS l USING (snapshot_bundle_id)
                JOIN source_blobs AS blobs ON blobs.blob_id = s.blob_id
                {where}
            ),
            latest AS (
                SELECT logical_source_id, max(capture_sequence) AS capture_sequence
                FROM candidate
                GROUP BY logical_source_id
            )
            SELECT candidate.snapshot_id, candidate.snapshot_bundle_id,
                candidate.source_id, candidate.agent_name, candidate.source_path,
                candidate.capture_sequence, candidate.captured_at, candidate.state,
                candidate.capture_status, candidate.original_byte_length,
                candidate.capture_sequence = latest.capture_sequence AS is_latest
            FROM candidate JOIN latest USING (logical_source_id)
            ORDER BY candidate.captured_at DESC, candidate.capture_sequence DESC,
                candidate.snapshot_id
            """,
            params,
        ).fetchall()
    return tuple(
        SnapshotSummary(
            snapshot_id=str(row[0]),
            snapshot_bundle_id=str(row[1]),
            source_id=str(row[2]),
            agent_name=str(row[3]),
            source_path=str(row[4]),
            capture_sequence=int(row[5]),
            captured_at=row[6],
            lifecycle_state=str(row[7]),
            capture_status=str(row[8]),
            byte_length=int(row[9]),
            is_latest=bool(row[10]),
        )
        for row in rows
    )


def snapshot_summary(database_path: Path, snapshot_id: str) -> SnapshotSummary | None:
    return next(
        (row for row in list_snapshots(database_path) if row.snapshot_id == snapshot_id), None
    )


def latest_snapshot(
    database_path: Path,
    source_id: str,
    *,
    lifecycle_state: str | None = None,
) -> SnapshotSummary | None:
    return next(
        (
            row
            for row in list_snapshots(database_path, lifecycle_state=lifecycle_state)
            if row.source_id == source_id and row.is_latest
        ),
        None,
    )


def prune_snapshot(database_path: Path, snapshot_id: str, *, force: bool) -> PruneResult:
    dependencies = snapshot_dependencies(database_path, snapshot_id)
    if dependencies.source_ids and not force:
        raise SnapshotPruneBlocked(snapshot_id, dependencies)

    bundle_id = dependencies.bundle_ids[0]
    with write_connection(database_path) as connection, transaction(connection):
        bundle_row = connection.execute(
            """
            SELECT previous_snapshot_bundle_id FROM snapshot_bundles
            WHERE snapshot_bundle_id = ?
            """,
            [bundle_id],
        ).fetchone()
        previous_bundle_id = bundle_row[0] if bundle_row else None
        lineage_row = connection.execute(
            """
            SELECT previous_lineage_bundle_id FROM bundle_capture_metadata
            WHERE snapshot_bundle_id = ?
            """,
            [bundle_id],
        ).fetchone()
        previous_lineage_bundle_id = lineage_row[0] if lineage_row else None
        member_snapshot_rows = connection.execute(
            """
            SELECT DISTINCT s.snapshot_id, s.logical_source_id, s.blob_id,
                s.previous_snapshot_id
            FROM snapshot_bundle_members AS m
            JOIN source_snapshots AS s ON s.snapshot_id = m.snapshot_id
            WHERE m.snapshot_bundle_id = ? ORDER BY s.snapshot_id
            """,
            [bundle_id],
        ).fetchall()

        for source_id in dependencies.source_ids:
            delete_source_records(connection, source_id)
        connection.execute(
            """
            UPDATE bundle_capture_metadata SET previous_lineage_bundle_id = ?
            WHERE previous_lineage_bundle_id = ?
            """,
            [previous_lineage_bundle_id, bundle_id],
        )
        connection.execute(
            """
            UPDATE snapshot_bundles SET previous_snapshot_bundle_id = ?
            WHERE previous_snapshot_bundle_id = ?
            """,
            [previous_bundle_id, bundle_id],
        )
        for table_name in (
            "lifecycle_observations",
            "bundle_member_capture_metadata",
            "bundle_capture_metadata",
            "snapshot_bundle_members",
        ):
            connection.execute(
                f"DELETE FROM {table_name} WHERE snapshot_bundle_id = ?",
                [bundle_id],
            )
        connection.execute(
            "DELETE FROM snapshot_bundles WHERE snapshot_bundle_id = ?",
            [bundle_id],
        )

        deleted_blob_ids: set[str] = set()
        for (
            member_snapshot_id,
            logical_source_id,
            blob_id,
            previous_snapshot_id,
        ) in member_snapshot_rows:
            remaining_bundle_references = connection.execute(
                "SELECT count(*) FROM snapshot_bundle_members WHERE snapshot_id = ?",
                [member_snapshot_id],
            ).fetchone()
            if remaining_bundle_references != (0,):
                continue
            connection.execute(
                "UPDATE source_snapshots SET previous_snapshot_id = ? "
                "WHERE previous_snapshot_id = ?",
                [previous_snapshot_id, member_snapshot_id],
            )
            connection.execute(
                "DELETE FROM source_snapshots WHERE snapshot_id = ?",
                [member_snapshot_id],
            )
            blob_references = connection.execute(
                "SELECT count(*) FROM source_snapshots WHERE blob_id = ?",
                [blob_id],
            ).fetchone()
            if blob_references == (0,):
                connection.execute("DELETE FROM source_blobs WHERE blob_id = ?", [blob_id])
                deleted_blob_ids.add(str(blob_id))
            source_references = connection.execute(
                "SELECT count(*) FROM source_snapshots WHERE logical_source_id = ?",
                [logical_source_id],
            ).fetchone()
            if source_references == (0,):
                connection.execute(
                    "DELETE FROM logical_sources WHERE logical_source_id = ?",
                    [logical_source_id],
                )

    checkpoint_completed = True
    try:
        with write_connection(database_path) as connection:
            connection.execute("CHECKPOINT")
    except duckdb.Error:
        checkpoint_completed = False
    return PruneResult(
        snapshot_id=snapshot_id,
        deleted_bundle_count=1,
        deleted_blob_count=len(deleted_blob_ids),
        dependent_source_ids=dependencies.source_ids,
        dependent_session_ids=dependencies.session_ids,
        dependent_analysis_run_ids=dependencies.analysis_run_ids,
        derived_row_counts=dependencies.derived_row_counts,
        forced=force,
        checkpoint_completed=checkpoint_completed,
    )

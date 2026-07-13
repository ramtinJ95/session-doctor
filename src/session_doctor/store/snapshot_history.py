from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    forced: bool


class SnapshotPruneBlocked(RuntimeError):
    def __init__(self, snapshot_id: str, dependent_source_ids: tuple[str, ...]) -> None:
        self.snapshot_id = snapshot_id
        self.dependent_source_ids = dependent_source_ids
        super().__init__(
            f"snapshot {snapshot_id} has dependent normalized sources: "
            f"{', '.join(dependent_source_ids)}"
        )


def snapshot_dependencies(database_path: Path, snapshot_id: str) -> tuple[str, ...]:
    with read_connection(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM source_snapshots WHERE snapshot_id = ?", [snapshot_id]
        ).fetchone()
        if exists is None:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        rows = connection.execute(
            """
            SELECT DISTINCT ss.source_id
            FROM session_sources AS ss
            JOIN snapshot_bundle_members AS m
              ON m.snapshot_bundle_id = ss.snapshot_bundle_id
            WHERE m.snapshot_id = ? OR ss.snapshot_id = ?
            ORDER BY ss.source_id
            """,
            [snapshot_id, snapshot_id],
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


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
            WITH latest AS (
                SELECT logical_source_id, max(capture_sequence) AS capture_sequence
                FROM source_snapshots
                GROUP BY logical_source_id
            )
            SELECT s.snapshot_id, b.snapshot_bundle_id, s.source_id, s.agent_name,
                s.source_path, s.capture_sequence, s.captured_at, l.state,
                c.capture_status, blobs.original_byte_length,
                s.capture_sequence = latest.capture_sequence AS is_latest
            FROM source_snapshots AS s
            JOIN snapshot_bundles AS b ON b.primary_snapshot_id = s.snapshot_id
            JOIN bundle_capture_metadata AS c USING (snapshot_bundle_id)
            JOIN lifecycle_observations AS l USING (snapshot_bundle_id)
            JOIN source_blobs AS blobs ON blobs.blob_id = s.blob_id
            JOIN latest USING (logical_source_id)
            {where}
            ORDER BY s.captured_at DESC, s.capture_sequence DESC, s.snapshot_id
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
    with read_connection(database_path) as connection:
        snapshot = connection.execute(
            """
            SELECT logical_source_id, blob_id, previous_snapshot_id
            FROM source_snapshots WHERE snapshot_id = ?
            """,
            [snapshot_id],
        ).fetchone()
        if snapshot is None:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        bundle_rows = connection.execute(
            """
            SELECT DISTINCT b.snapshot_bundle_id, b.previous_snapshot_bundle_id
            FROM snapshot_bundles AS b
            JOIN snapshot_bundle_members AS m USING (snapshot_bundle_id)
            WHERE m.snapshot_id = ? OR b.primary_snapshot_id = ?
            ORDER BY b.snapshot_bundle_id
            """,
            [snapshot_id, snapshot_id],
        ).fetchall()
        primary_bundle = connection.execute(
            "SELECT 1 FROM snapshot_bundles WHERE primary_snapshot_id = ? LIMIT 1",
            [snapshot_id],
        ).fetchone()
        if primary_bundle is None:
            raise ValueError(
                "Only primary snapshots can be pruned; prune the owning bundle's primary"
            )
        bundle_ids = [str(row[0]) for row in bundle_rows]
        member_snapshot_rows = connection.execute(
            """
            SELECT DISTINCT s.snapshot_id, s.logical_source_id, s.blob_id,
                s.previous_snapshot_id
            FROM snapshot_bundle_members AS m
            JOIN source_snapshots AS s ON s.snapshot_id = m.snapshot_id
            WHERE m.snapshot_bundle_id IN (SELECT unnest(?))
            ORDER BY s.snapshot_id
            """,
            [bundle_ids],
        ).fetchall()
        dependent_rows = (
            connection.execute(
                """
                SELECT DISTINCT source_id FROM session_sources
                WHERE snapshot_id = ? OR snapshot_bundle_id IN (
                    SELECT unnest(?)
                )
                ORDER BY source_id
                """,
                [snapshot_id, bundle_ids],
            ).fetchall()
            if bundle_ids
            else []
        )
        dependent_source_ids = tuple(str(row[0]) for row in dependent_rows)
    if dependent_source_ids and not force:
        raise SnapshotPruneBlocked(snapshot_id, dependent_source_ids)

    with write_connection(database_path) as connection, transaction(connection):
        for source_id in dependent_source_ids:
            delete_source_records(connection, source_id)
        for bundle_id, previous_bundle_id in bundle_rows:
            lineage_row = connection.execute(
                """
                SELECT previous_lineage_bundle_id FROM bundle_capture_metadata
                WHERE snapshot_bundle_id = ?
                """,
                [bundle_id],
            ).fetchone()
            previous_lineage_bundle_id = lineage_row[0] if lineage_row else None
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
            connection.execute(
                "DELETE FROM lifecycle_observations WHERE snapshot_bundle_id = ?",
                [bundle_id],
            )
            connection.execute(
                "DELETE FROM bundle_member_capture_metadata WHERE snapshot_bundle_id = ?",
                [bundle_id],
            )
            connection.execute(
                "DELETE FROM bundle_capture_metadata WHERE snapshot_bundle_id = ?",
                [bundle_id],
            )
            connection.execute(
                "DELETE FROM snapshot_bundle_members WHERE snapshot_bundle_id = ?",
                [bundle_id],
            )

    # DuckDB's foreign-key indexes are updated at transaction boundaries. Parent
    # bundle and snapshot deletion therefore happens in deliberate committed stages.
    with write_connection(database_path) as connection, transaction(connection):
        for bundle_id, _previous_bundle_id in bundle_rows:
            connection.execute(
                "DELETE FROM snapshot_bundles WHERE snapshot_bundle_id = ?",
                [bundle_id],
            )

    with write_connection(database_path) as connection, transaction(connection):
        for (
            member_snapshot_id,
            _logical_source_id,
            _blob_id,
            previous_snapshot_id,
        ) in member_snapshot_rows:
            connection.execute(
                """
                UPDATE source_snapshots SET previous_snapshot_id = ?
                WHERE previous_snapshot_id = ?
                """,
                [previous_snapshot_id, member_snapshot_id],
            )
            connection.execute(
                "DELETE FROM source_snapshots WHERE snapshot_id = ?",
                [member_snapshot_id],
            )

    with write_connection(database_path) as connection, transaction(connection):
        deleted_blob_count = 0
        for _member_snapshot_id, logical_source_id, blob_id, _previous_id in member_snapshot_rows:
            blob_references = connection.execute(
                "SELECT count(*) FROM source_snapshots WHERE blob_id = ?", [blob_id]
            ).fetchone()
            if blob_references == (0,):
                connection.execute("DELETE FROM source_blobs WHERE blob_id = ?", [blob_id])
                deleted_blob_count += 1
            source_references = connection.execute(
                "SELECT count(*) FROM source_snapshots WHERE logical_source_id = ?",
                [logical_source_id],
            ).fetchone()
            if source_references == (0,):
                connection.execute(
                    "DELETE FROM logical_sources WHERE logical_source_id = ?",
                    [logical_source_id],
                )
    with write_connection(database_path) as connection:
        connection.execute("CHECKPOINT")
    return PruneResult(
        snapshot_id=snapshot_id,
        deleted_bundle_count=len(bundle_ids),
        deleted_blob_count=deleted_blob_count,
        dependent_source_ids=dependent_source_ids,
        forced=force,
    )

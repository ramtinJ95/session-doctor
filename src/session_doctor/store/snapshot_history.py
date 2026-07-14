from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import duckdb
from duckdb import DuckDBPyConnection

from session_doctor.ids import stable_id

from .connection import read_connection, transaction, write_connection
from .json_values import metadata_json
from .lifecycle import LIFECYCLE_STATES
from .writers import delete_source_records


@dataclass(frozen=True)
class SnapshotSummary:
    snapshot_id: str
    snapshot_bundle_id: str | None
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
    dependent_normalization_run_ids: tuple[str, ...]
    dependent_evaluation_packet_ids: tuple[str, ...]
    dependent_evaluation_corpus_ids: tuple[str, ...]
    partial_evaluation_corpus_ids: tuple[str, ...]
    dependent_audit_protocol_ids: tuple[str, ...]
    partial_audit_protocol_ids: tuple[str, ...]
    inbound_source_ids: tuple[str, ...]
    inbound_session_ids: tuple[str, ...]
    downstream_lifecycle_bundle_ids: tuple[str, ...]
    derived_row_counts: dict[str, int]
    forced: bool
    checkpoint_completed: bool


@dataclass(frozen=True)
class PruneDependencies:
    bundle_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    session_ids: tuple[str, ...]
    analysis_run_ids: tuple[str, ...]
    normalization_run_ids: tuple[str, ...]
    evaluation_packet_ids: tuple[str, ...]
    evaluation_corpus_ids: tuple[str, ...]
    partial_evaluation_corpus_ids: tuple[str, ...]
    audit_protocol_ids: tuple[str, ...]
    partial_audit_protocol_ids: tuple[str, ...]
    inbound_source_ids: tuple[str, ...]
    inbound_session_ids: tuple[str, ...]
    downstream_lifecycle_bundle_ids: tuple[str, ...]
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
        return _snapshot_dependencies(connection, snapshot_id)


def _snapshot_dependencies(connection: DuckDBPyConnection, snapshot_id: str) -> PruneDependencies:
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
        member_owner = connection.execute(
            "SELECT snapshot_bundle_id FROM snapshot_bundle_members WHERE snapshot_id = ? LIMIT 1",
            [snapshot_id],
        ).fetchone()
        if member_owner is not None:
            raise ValueError(
                "Only primary snapshots can be pruned; prune the owning bundle's primary"
            )
        return PruneDependencies(
            bundle_ids=(),
            source_ids=(),
            session_ids=(),
            analysis_run_ids=(),
            normalization_run_ids=(),
            evaluation_packet_ids=(),
            evaluation_corpus_ids=(),
            partial_evaluation_corpus_ids=(),
            audit_protocol_ids=(),
            partial_audit_protocol_ids=(),
            inbound_source_ids=(),
            inbound_session_ids=(),
            downstream_lifecycle_bundle_ids=(),
            derived_row_counts={},
        )
    if len(bundle_rows) != 1:
        raise RuntimeError(f"Snapshot has multiple owning bundles: {snapshot_id}")
    bundle_ids = tuple(str(row[0]) for row in bundle_rows)
    normalization_rows = connection.execute(
        """
        SELECT normalization_run_id FROM normalization_run_bundles
        WHERE snapshot_bundle_id IN (SELECT unnest(?))
        ORDER BY normalization_run_id
        """,
        [list(bundle_ids)],
    ).fetchall()
    normalization_run_ids = tuple(str(row[0]) for row in normalization_rows)
    evaluation_packet_rows = connection.execute(
        "SELECT packet_id FROM evaluation_packets "
        "WHERE snapshot_bundle_id IN (SELECT unnest(?)) ORDER BY packet_id",
        [list(bundle_ids)],
    ).fetchall()
    evaluation_packet_ids = tuple(str(row[0]) for row in evaluation_packet_rows)
    evaluation_corpus_ids: tuple[str, ...] = ()
    partial_evaluation_corpus_ids: tuple[str, ...] = ()
    if evaluation_packet_ids:
        corpus_rows = connection.execute(
            "SELECT DISTINCT evaluation_corpus_id FROM evaluation_packets "
            "WHERE packet_id IN (SELECT unnest(?)) ORDER BY evaluation_corpus_id",
            [list(evaluation_packet_ids)],
        ).fetchall()
        evaluation_corpus_ids = tuple(str(row[0]) for row in corpus_rows)
        partial_corpora = []
        for corpus_id in evaluation_corpus_ids:
            remaining = connection.execute(
                "SELECT count(*) FROM evaluation_packets WHERE evaluation_corpus_id = ? "
                "AND packet_id NOT IN (SELECT unnest(?))",
                [corpus_id, list(evaluation_packet_ids)],
            ).fetchone()
            if remaining is not None and int(remaining[0]) > 0:
                partial_corpora.append(corpus_id)
        partial_evaluation_corpus_ids = tuple(partial_corpora)
    audit_protocol_ids: tuple[str, ...] = ()
    partial_audit_protocol_ids: tuple[str, ...] = ()
    if evaluation_packet_ids:
        packet_id_set = set(evaluation_packet_ids)
        matching_protocols = []
        partial_protocols = []
        for protocol_id, cohort_json in connection.execute(
            "SELECT audit_protocol_id, cohort_packet_ids_json FROM audit_protocols "
            "ORDER BY annotation_protocol_version"
        ).fetchall():
            referenced = set(json.loads(str(cohort_json)))
            if referenced & packet_id_set:
                matching_protocols.append(str(protocol_id))
                if referenced - packet_id_set:
                    partial_protocols.append(str(protocol_id))
        audit_protocol_ids = tuple(matching_protocols)
        partial_audit_protocol_ids = tuple(partial_protocols)
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
    inbound_source_rows = (
        connection.execute(
            "SELECT source_id FROM session_sources "
            "WHERE parent_source_id IN (SELECT unnest(?)) ORDER BY source_id",
            [list(source_ids)],
        ).fetchall()
        if source_ids
        else []
    )
    inbound_source_ids = tuple(str(row[0]) for row in inbound_source_rows)
    inbound_session_rows = (
        connection.execute(
            "SELECT session_id FROM sessions "
            "WHERE parent_session_id IN (SELECT unnest(?)) ORDER BY session_id",
            [list(session_ids)],
        ).fetchall()
        if session_ids
        else []
    )
    inbound_session_ids = tuple(str(row[0]) for row in inbound_session_rows)
    downstream_lifecycle_rows = connection.execute(
        """
        SELECT snapshot_bundle_id FROM bundle_capture_metadata
        WHERE previous_lineage_bundle_id IN (SELECT unnest(?))
        ORDER BY snapshot_bundle_id
        """,
        [list(bundle_ids)],
    ).fetchall()
    downstream_lifecycle_bundle_ids = tuple(str(row[0]) for row in downstream_lifecycle_rows)
    semantic_analysis_rows = (
        connection.execute(
            """
            WITH RECURSIVE affected (analysis_identity) AS (
                SELECT a.analysis_identity
                FROM semantic_analysis_runs AS a
                JOIN lifecycle_observations AS l USING (lifecycle_observation_id)
                WHERE l.snapshot_bundle_id IN (SELECT unnest(?))
                UNION
                SELECT delegation.child_analysis_identity
                FROM episode_delegations AS delegation
                JOIN affected
                  ON delegation.parent_analysis_identity = affected.analysis_identity
            )
            SELECT DISTINCT analysis_identity FROM affected ORDER BY analysis_identity
            """,
            [list((*bundle_ids, *downstream_lifecycle_bundle_ids))],
        ).fetchall()
        if normalization_run_ids
        else []
    )
    analysis_run_ids = tuple(str(row[0]) for row in semantic_analysis_rows)
    derived_row_counts: dict[str, int] = {
        "session_sources": len(source_ids),
        "sessions": len(session_ids),
    }
    normalized_entity_count = (
        connection.execute(
            "SELECT count(*) FROM normalized_entities "
            "WHERE normalization_run_id IN (SELECT unnest(?))",
            [list(normalization_run_ids)],
        ).fetchone()
        if normalization_run_ids
        else None
    )
    derived_row_counts["normalized_entities"] = (
        int(normalized_entity_count[0]) if normalized_entity_count else 0
    )
    derived_row_counts["normalization_semantics"] = len(normalization_run_ids)
    derived_row_counts["semantic_analysis_runs"] = len(semantic_analysis_rows)
    for table_name, identity_column in (
        ("episode_analysis_runs", "analysis_identity"),
        ("episodes", "analysis_identity"),
        ("episode_boundaries", "analysis_identity"),
        ("episode_observations", "analysis_identity"),
        ("episode_entity_memberships", "analysis_identity"),
        ("episode_delegations", "child_analysis_identity"),
    ):
        count_row = (
            connection.execute(
                f"SELECT count(*) FROM {table_name} WHERE {identity_column} IN (SELECT unnest(?))",
                [list(analysis_run_ids)],
            ).fetchone()
            if analysis_run_ids
            else None
        )
        derived_row_counts[table_name] = int(count_row[0]) if count_row else 0
    for table_name in (
        "judge_annotations",
        "judge_panel_resolutions",
        "audit_selections",
        "human_adjudications",
        "reference_resolutions",
    ):
        count_row = (
            connection.execute(
                f"SELECT count(*) FROM {table_name} WHERE packet_id IN (SELECT unnest(?))",
                [list(evaluation_packet_ids)],
            ).fetchone()
            if evaluation_packet_ids
            else None
        )
        derived_row_counts[table_name] = int(count_row[0]) if count_row else 0
    derived_row_counts["audit_protocols"] = len(audit_protocol_ids)
    derived_row_counts["evaluation_corpora"] = len(evaluation_corpus_ids)
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
        normalization_run_ids=normalization_run_ids,
        evaluation_packet_ids=evaluation_packet_ids,
        evaluation_corpus_ids=evaluation_corpus_ids,
        partial_evaluation_corpus_ids=partial_evaluation_corpus_ids,
        audit_protocol_ids=audit_protocol_ids,
        partial_audit_protocol_ids=partial_audit_protocol_ids,
        inbound_source_ids=inbound_source_ids,
        inbound_session_ids=inbound_session_ids,
        downstream_lifecycle_bundle_ids=downstream_lifecycle_bundle_ids,
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
    clauses: list[str] = [
        "(b.snapshot_bundle_id IS NOT NULL OR NOT EXISTS ("
        "SELECT 1 FROM snapshot_bundle_members AS ownership "
        "WHERE ownership.snapshot_id = s.snapshot_id))"
    ]
    params: list[object] = []
    if agent_name is not None:
        clauses.append("s.agent_name = ?")
        params.append(agent_name)
    if lifecycle_state is not None:
        clauses.append("coalesce(l.state, 'snapshot_incomplete') = ?")
        params.append(lifecycle_state)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            WITH candidate AS (
                SELECT s.snapshot_id, b.snapshot_bundle_id, s.source_id,
                    s.agent_name, s.source_path, s.logical_source_id,
                    s.capture_sequence, s.captured_at,
                    coalesce(l.state, 'snapshot_incomplete') AS state,
                    coalesce(c.capture_status, 'unbundled') AS capture_status,
                    blobs.original_byte_length
                FROM source_snapshots AS s
                LEFT JOIN snapshot_bundles AS b ON b.primary_snapshot_id = s.snapshot_id
                LEFT JOIN bundle_capture_metadata AS c USING (snapshot_bundle_id)
                LEFT JOIN lifecycle_observations AS l USING (snapshot_bundle_id)
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
            snapshot_bundle_id=str(row[1]) if row[1] is not None else None,
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
    with write_connection(database_path) as connection, transaction(connection):
        dependencies = _snapshot_dependencies(connection, snapshot_id)
        if dependencies.partial_audit_protocol_ids or dependencies.partial_evaluation_corpus_ids:
            raise SnapshotPruneBlocked(snapshot_id, dependencies)
        if (
            dependencies.source_ids
            or dependencies.normalization_run_ids
            or dependencies.analysis_run_ids
            or dependencies.evaluation_packet_ids
            or dependencies.evaluation_corpus_ids
            or dependencies.audit_protocol_ids
            or dependencies.inbound_source_ids
            or dependencies.inbound_session_ids
            or dependencies.downstream_lifecycle_bundle_ids
        ) and not force:
            raise SnapshotPruneBlocked(snapshot_id, dependencies)
        orphan_snapshot = not dependencies.bundle_ids
        if orphan_snapshot:
            orphan_row = connection.execute(
                """
                SELECT agent_name, logical_source_id, source_id, source_path,
                    captured_at, native_modified_at, snapshot_content_id
                FROM source_snapshots WHERE snapshot_id = ?
                """,
                [snapshot_id],
            ).fetchone()
            if orphan_row is None:
                raise ValueError(f"Snapshot not found: {snapshot_id}")
            (
                orphan_agent,
                orphan_logical_source_id,
                orphan_source_id,
                orphan_source_path,
                orphan_captured_at,
                orphan_modified_at,
                orphan_content_id,
            ) = orphan_row
            bundle_id = stable_id("orphan-prune-bundle", snapshot_id)
            native_identity = f"orphan-prune:{snapshot_id}"
            connection.execute(
                """
                INSERT INTO snapshot_bundles VALUES (
                    ?, ?, ?, ?, ?, 'fallback_parse_failed', 1, NULL, ?
                )
                """,
                [
                    bundle_id,
                    stable_id("orphan-prune-content", orphan_content_id),
                    orphan_agent,
                    native_identity,
                    snapshot_id,
                    orphan_captured_at,
                ],
            )
            connection.execute(
                """
                INSERT INTO bundle_capture_metadata VALUES (
                    ?, ?, 1, NULL, ?, ?, 'parse_failed', ?
                )
                """,
                [
                    bundle_id,
                    stable_id("bundle-lineage", orphan_agent, native_identity),
                    orphan_captured_at,
                    orphan_captured_at,
                    metadata_json({"reason": "orphan_prune"}),
                ],
            )
            connection.execute(
                """
                INSERT INTO bundle_member_capture_metadata VALUES (
                    ?, 0, ?, ?, ?, ?, 'primary', 'captured', ?, ?, ?, ?, '{}'
                )
                """,
                [
                    bundle_id,
                    orphan_logical_source_id,
                    snapshot_id,
                    orphan_source_id,
                    orphan_source_path,
                    orphan_captured_at,
                    orphan_captured_at,
                    orphan_modified_at,
                    orphan_modified_at,
                ],
            )
            connection.execute(
                "INSERT INTO snapshot_bundle_members VALUES (?, ?, ?, 0, 'primary', 'captured')",
                [bundle_id, orphan_logical_source_id, snapshot_id],
            )
            dependencies = replace(dependencies, bundle_ids=(bundle_id,))
        bundle_id = dependencies.bundle_ids[0]
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
        downstream_settled_rows = (
            connection.execute(
                """
                SELECT l.snapshot_bundle_id, b.captured_at
                FROM lifecycle_observations AS l
                JOIN snapshot_bundles AS b USING (snapshot_bundle_id)
                WHERE l.snapshot_bundle_id IN (SELECT unnest(?))
                    AND l.state = 'settled_unknown'
                ORDER BY l.snapshot_bundle_id
                """,
                [list(dependencies.downstream_lifecycle_bundle_ids)],
            ).fetchall()
            if dependencies.downstream_lifecycle_bundle_ids
            else []
        )
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

        if dependencies.analysis_run_ids:
            analysis_run_ids = list(dependencies.analysis_run_ids)
            connection.execute(
                "DELETE FROM episode_delegations "
                "WHERE child_analysis_identity IN (SELECT unnest(?)) "
                "OR parent_analysis_identity IN (SELECT unnest(?))",
                [analysis_run_ids, analysis_run_ids],
            )
            for table_name in (
                "episode_entity_memberships",
                "episode_observations",
                "episode_boundaries",
                "episodes",
                "episode_analysis_runs",
            ):
                connection.execute(
                    f"DELETE FROM {table_name} WHERE analysis_identity IN (SELECT unnest(?))",
                    [analysis_run_ids],
                )
            connection.execute(
                "DELETE FROM semantic_analysis_runs WHERE analysis_identity IN (SELECT unnest(?))",
                [analysis_run_ids],
            )
        if dependencies.evaluation_packet_ids:
            if dependencies.audit_protocol_ids:
                connection.execute(
                    "DELETE FROM audit_protocols WHERE audit_protocol_id IN (SELECT unnest(?))",
                    [list(dependencies.audit_protocol_ids)],
                )
            for table_name in (
                "reference_resolutions",
                "human_adjudications",
                "audit_selections",
                "judge_panel_resolutions",
                "judge_annotations",
            ):
                connection.execute(
                    f"DELETE FROM {table_name} WHERE packet_id IN (SELECT unnest(?))",
                    [list(dependencies.evaluation_packet_ids)],
                )
            connection.execute(
                "DELETE FROM evaluation_packets WHERE packet_id IN (SELECT unnest(?))",
                [list(dependencies.evaluation_packet_ids)],
            )
            connection.execute(
                "DELETE FROM evaluation_corpora WHERE evaluation_corpus_id IN (SELECT unnest(?))",
                [list(dependencies.evaluation_corpus_ids)],
            )

        connection.execute(
            "DELETE FROM normalization_run_bundles WHERE snapshot_bundle_id = ?",
            [bundle_id],
        )
        for normalization_run_id in dependencies.normalization_run_ids:
            remaining_run_bundles = connection.execute(
                "SELECT count(*) FROM normalization_run_bundles WHERE normalization_run_id = ?",
                [normalization_run_id],
            ).fetchone()
            if remaining_run_bundles != (0,):
                continue
            connection.execute(
                "DELETE FROM normalized_entities WHERE normalization_run_id = ?",
                [normalization_run_id],
            )
            connection.execute(
                "DELETE FROM semantic_analysis_runs WHERE normalization_run_id = ?",
                [normalization_run_id],
            )
            connection.execute(
                "DELETE FROM normalization_semantics WHERE normalization_run_id = ?",
                [normalization_run_id],
            )
            connection.execute(
                "DELETE FROM normalization_runs WHERE normalization_run_id = ?",
                [normalization_run_id],
            )

        for source_id in dependencies.source_ids:
            delete_source_records(connection, source_id)
        if dependencies.inbound_source_ids:
            connection.execute(
                "UPDATE session_sources SET parent_source_id = NULL "
                "WHERE source_id IN (SELECT unnest(?))",
                [list(dependencies.inbound_source_ids)],
            )
        if dependencies.inbound_session_ids:
            connection.execute(
                "UPDATE sessions SET parent_session_id = NULL "
                "WHERE session_id IN (SELECT unnest(?))",
                [list(dependencies.inbound_session_ids)],
            )
        connection.execute(
            """
            UPDATE bundle_capture_metadata SET previous_lineage_bundle_id = ?
            WHERE previous_lineage_bundle_id = ?
            """,
            [previous_lineage_bundle_id, bundle_id],
        )
        for downstream_bundle_id, downstream_captured_at in downstream_settled_rows:
            connection.execute(
                """
                DELETE FROM semantic_analysis_runs
                WHERE lifecycle_observation_id IN (
                    SELECT lifecycle_observation_id FROM lifecycle_observations
                    WHERE snapshot_bundle_id = ?
                )
                """,
                [downstream_bundle_id],
            )
            connection.execute(
                "DELETE FROM lifecycle_observations WHERE snapshot_bundle_id = ?",
                [downstream_bundle_id],
            )
            state = "possibly_active"
            connection.execute(
                """
                INSERT INTO lifecycle_observations (
                    lifecycle_observation_id, snapshot_bundle_id,
                    lifecycle_policy_version, state, observed_at, evidence_json
                ) VALUES (?, ?, 'lifecycle-v1', ?, ?, ?)
                """,
                [
                    stable_id(
                        "lifecycle-observation",
                        downstream_bundle_id,
                        "lifecycle-v1",
                        state,
                    ),
                    downstream_bundle_id,
                    state,
                    downstream_captured_at,
                    metadata_json(
                        {
                            "reason": "predecessor_pruned",
                            "pruned_snapshot_bundle_id": bundle_id,
                        }
                    ),
                ],
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
        deleted_bundle_count=0 if orphan_snapshot else 1,
        deleted_blob_count=len(deleted_blob_ids),
        dependent_source_ids=dependencies.source_ids,
        dependent_session_ids=dependencies.session_ids,
        dependent_analysis_run_ids=dependencies.analysis_run_ids,
        dependent_normalization_run_ids=dependencies.normalization_run_ids,
        dependent_evaluation_packet_ids=dependencies.evaluation_packet_ids,
        dependent_evaluation_corpus_ids=dependencies.evaluation_corpus_ids,
        partial_evaluation_corpus_ids=dependencies.partial_evaluation_corpus_ids,
        dependent_audit_protocol_ids=dependencies.audit_protocol_ids,
        partial_audit_protocol_ids=dependencies.partial_audit_protocol_ids,
        inbound_source_ids=dependencies.inbound_source_ids,
        inbound_session_ids=dependencies.inbound_session_ids,
        downstream_lifecycle_bundle_ids=dependencies.downstream_lifecycle_bundle_ids,
        derived_row_counts=dependencies.derived_row_counts,
        forced=force,
        checkpoint_completed=checkpoint_completed,
    )

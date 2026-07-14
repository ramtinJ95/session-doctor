from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from session_doctor.schemas import SemanticAnalysisComponents, SemanticFoundation
from session_doctor.semantic_foundations import semantic_analysis_identity

from .connection import read_connection, transaction, write_connection
from .json_values import metadata_json, parse_metadata


@dataclass(frozen=True)
class SemanticAnalysisRun:
    analysis_identity: str
    components: SemanticAnalysisComponents
    started_at: object | None
    completed_at: object | None
    metadata: dict[str, object]


class SemanticAnalysisConflictError(RuntimeError):
    pass


def record_semantic_analysis_run(
    database_path: Path,
    components: SemanticAnalysisComponents,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> SemanticAnalysisRun:
    with write_connection(database_path) as connection, transaction(connection):
        return record_semantic_analysis_run_rows(
            connection,
            components,
            started_at=started_at,
            completed_at=completed_at,
            metadata=metadata,
        )


def record_semantic_analysis_run_rows(
    connection: duckdb.DuckDBPyConnection,
    components: SemanticAnalysisComponents,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> SemanticAnalysisRun:
    analysis_identity = semantic_analysis_identity(components)
    values = (
        analysis_identity,
        components.normalization_run_id,
        components.lifecycle_observation_id,
        components.lifecycle_policy_version,
        components.ordering_version,
        components.segmentation_version,
        components.relation_rule_set_version,
        components.result_rule_set_version,
        components.finding_rule_set_version,
        components.facet_policy_version,
        components.configuration_hash,
        started_at,
        completed_at,
        metadata_json(metadata or {}),
    )
    normalization_row = connection.execute(
        "SELECT foundation_json FROM normalization_semantics WHERE normalization_run_id = ?",
        [components.normalization_run_id],
    ).fetchone()
    if normalization_row is None:
        raise SemanticAnalysisConflictError("normalization foundation is missing")
    lifecycle_row = connection.execute(
        """
        SELECT l.lifecycle_policy_version
        FROM lifecycle_observations AS l
        JOIN normalization_run_bundles AS n USING (snapshot_bundle_id)
        WHERE l.lifecycle_observation_id = ? AND n.normalization_run_id = ?
        """,
        [
            components.lifecycle_observation_id,
            components.normalization_run_id,
        ],
    ).fetchone()
    if lifecycle_row != (components.lifecycle_policy_version,):
        raise SemanticAnalysisConflictError("lifecycle observation is incompatible")
    foundation = SemanticFoundation.model_validate_json(str(normalization_row[0]))
    if foundation.ordering.ordering_version != components.ordering_version:
        raise SemanticAnalysisConflictError("ordering projection is incompatible")
    connection.execute(
        "INSERT INTO semantic_analysis_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        list(values),
    )
    stored = connection.execute(
        "SELECT * FROM semantic_analysis_runs WHERE analysis_identity = ?",
        [analysis_identity],
    ).fetchone()
    if stored is None or tuple(stored[:11]) != values[:11]:
        raise SemanticAnalysisConflictError("analysis identity collision")
    return SemanticAnalysisRun(
        analysis_identity=analysis_identity,
        components=components,
        started_at=stored[11],
        completed_at=stored[12],
        metadata=parse_metadata(stored[13]),
    )


def list_semantic_analysis_runs(database_path: Path) -> tuple[SemanticAnalysisRun, ...]:
    with read_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM semantic_analysis_runs ORDER BY analysis_identity"
        ).fetchall()
    return tuple(semantic_analysis_run_from_row(row) for row in rows)


def semantic_analysis_run_from_row(row: tuple[object, ...]) -> SemanticAnalysisRun:
    components = SemanticAnalysisComponents(
        normalization_run_id=str(row[1]),
        lifecycle_observation_id=str(row[2]),
        lifecycle_policy_version=str(row[3]),
        ordering_version=str(row[4]),
        segmentation_version=str(row[5]),
        relation_rule_set_version=str(row[6]),
        result_rule_set_version=str(row[7]),
        finding_rule_set_version=str(row[8]),
        facet_policy_version=str(row[9]),
        configuration_hash=str(row[10]),
    )
    return SemanticAnalysisRun(
        analysis_identity=str(row[0]),
        components=components,
        started_at=row[11],
        completed_at=row[12],
        metadata=parse_metadata(row[13]),
    )

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

import duckdb

from session_doctor.analysis.version import ANALYZER_VERSION
from session_doctor.diagnostic_models import (
    DiagnosticAnalysis,
    DiagnosticIndexes,
    DiagnosticSnapshot,
    NormalizedSessionData,
    TopologyReference,
    UnresolvedDiagnosticReferences,
    immutable_index,
    immutable_record_index,
)
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    MessageFeature,
    SessionClassification,
    SessionFeature,
)

from .analysis_readers import AnalysisCompatibility, analysis_compatibility
from .connection import read_connection, transaction
from .json_values import parse_metadata, parse_string_list
from .row_loaders import (
    load_command_runs,
    load_file_activities,
    load_messages,
    load_model_usage,
    load_parse_warnings,
    load_raw_events,
    load_session,
    load_tool_calls,
    load_tool_results,
)


def load_diagnostic_snapshot(
    database_path: Path,
    session_id: str,
) -> DiagnosticSnapshot | None:
    with read_connection(database_path) as connection, transaction(connection):
        session = load_session(connection, session_id)
        if session is None:
            return None
        normalized = NormalizedSessionData(
            session=session,
            raw_events=tuple(load_raw_events(connection, session.source_id)),
            messages=tuple(load_messages(connection, session_id)),
            tool_calls=tuple(load_tool_calls(connection, session_id)),
            tool_results=tuple(load_tool_results(connection, session_id)),
            command_runs=tuple(load_command_runs(connection, session_id)),
            file_activities=tuple(load_file_activities(connection, session_id)),
            model_usage=tuple(load_model_usage(connection, session_id)),
            parse_warnings=tuple(load_parse_warnings(connection, session.source_id)),
        )
        topology_references = load_topology_references(
            connection, session_id, session.parent_session_id
        )
        analysis = load_diagnostic_analysis(connection, session_id)
        indexes = build_indexes(normalized)
        return DiagnosticSnapshot(
            normalized=normalized,
            topology_references=topology_references,
            analysis=analysis,
            indexes=indexes,
            unresolved=find_unresolved_references(normalized, analysis, indexes),
        )


def load_topology_references(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
    parent_session_id: str | None,
) -> tuple[TopologyReference, ...]:
    references: list[TopologyReference] = []
    if parent_session_id is not None:
        parent = connection.execute(
            "SELECT agent_name, is_sidechain FROM sessions WHERE session_id = ?",
            [parent_session_id],
        ).fetchone()
        references.append(
            TopologyReference(
                session_id=parent_session_id,
                relationship="parent",
                agent_name=AgentName(parent[0]) if parent else None,
                is_sidechain=bool(parent[1]) if parent else None,
                exists=parent is not None,
            )
        )
    child_rows = connection.execute(
        """
        SELECT session_id, agent_name, is_sidechain
        FROM sessions
        WHERE parent_session_id = ?
        ORDER BY session_id
        """,
        [session_id],
    ).fetchall()
    references.extend(
        TopologyReference(
            session_id=str(row[0]),
            relationship="child",
            agent_name=AgentName(row[1]),
            is_sidechain=bool(row[2]),
            exists=True,
        )
        for row in child_rows
    )
    return tuple(references)


def load_diagnostic_analysis(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
) -> DiagnosticAnalysis:
    latest = load_latest_analysis_run(connection, session_id)
    observed_version = latest.analyzer_version if latest else None
    compatibility = analysis_compatibility(observed_version)
    if compatibility is not AnalysisCompatibility.CURRENT or latest is None:
        return DiagnosticAnalysis(
            compatibility=compatibility,
            current_analyzer_version=ANALYZER_VERSION,
            observed_analyzer_version=observed_version,
            analysis_run_id=latest.analysis_run_id if latest else None,
            action=f"session-doctor analyze {session_id}",
            message_features=(),
            session_features=(),
            classifications=(),
        )
    return DiagnosticAnalysis(
        compatibility=compatibility,
        current_analyzer_version=ANALYZER_VERSION,
        observed_analyzer_version=observed_version,
        analysis_run_id=latest.analysis_run_id,
        action=None,
        message_features=load_message_features(connection, latest.analysis_run_id),
        session_features=load_session_features(connection, latest.analysis_run_id),
        classifications=load_classifications(connection, latest.analysis_run_id),
    )


def load_latest_analysis_run(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
) -> AnalysisRun | None:
    row = connection.execute(
        """
        SELECT analysis_run_id, session_id, started_at, completed_at, analyzer_version
        FROM analysis_runs
        WHERE session_id = ?
        ORDER BY completed_at DESC NULLS LAST,
            started_at DESC NULLS LAST,
            analysis_run_id DESC
        LIMIT 1
        """,
        [session_id],
    ).fetchone()
    if row is None:
        return None
    return AnalysisRun(
        analysis_run_id=str(row[0]),
        session_id=str(row[1]),
        started_at=row[2],
        completed_at=row[3],
        analyzer_version=str(row[4]),
    )


def load_message_features(
    connection: duckdb.DuckDBPyConnection,
    analysis_run_id: str,
) -> tuple[MessageFeature, ...]:
    rows = connection.execute(
        """
        SELECT mf.message_feature_id, mf.analysis_run_id, mf.session_id, mf.message_id,
            mf.source_event_id, mf.feature_name, mf.feature_value, mf.score,
            mf.evidence_json, mf.metadata_json
        FROM message_features AS mf
        LEFT JOIN raw_events AS e ON e.event_id = mf.source_event_id
        WHERE mf.analysis_run_id = ?
        ORDER BY e.record_index NULLS LAST, mf.message_feature_id
        """,
        [analysis_run_id],
    ).fetchall()
    return tuple(
        MessageFeature(
            message_feature_id=str(row[0]),
            analysis_run_id=str(row[1]),
            session_id=str(row[2]),
            message_id=str(row[3]),
            source_event_id=row[4],
            feature_name=str(row[5]),
            feature_value=str(row[6]),
            score=float(row[7]),
            evidence=parse_metadata(row[8]),
            metadata=parse_metadata(row[9]),
        )
        for row in rows
    )


def load_session_features(
    connection: duckdb.DuckDBPyConnection,
    analysis_run_id: str,
) -> tuple[SessionFeature, ...]:
    rows = connection.execute(
        """
        SELECT session_feature_id, analysis_run_id, session_id, feature_name,
            feature_value, score, evidence_json, metadata_json
        FROM session_features
        WHERE analysis_run_id = ?
        ORDER BY feature_name, session_feature_id
        """,
        [analysis_run_id],
    ).fetchall()
    return tuple(
        SessionFeature(
            session_feature_id=str(row[0]),
            analysis_run_id=str(row[1]),
            session_id=str(row[2]),
            feature_name=str(row[3]),
            feature_value=str(row[4]),
            score=float(row[5]),
            evidence=parse_metadata(row[6]),
            metadata=parse_metadata(row[7]),
        )
        for row in rows
    )


def load_classifications(
    connection: duckdb.DuckDBPyConnection,
    analysis_run_id: str,
) -> tuple[SessionClassification, ...]:
    rows = connection.execute(
        """
        SELECT session_classification_id, analysis_run_id, session_id, label,
            score, confidence, evidence_event_ids_json, evidence_summary, metadata_json
        FROM session_classifications
        WHERE analysis_run_id = ?
        ORDER BY score DESC, label, session_classification_id
        """,
        [analysis_run_id],
    ).fetchall()
    return tuple(
        SessionClassification(
            session_classification_id=str(row[0]),
            analysis_run_id=str(row[1]),
            session_id=str(row[2]),
            label=str(row[3]),
            score=float(row[4]),
            confidence=float(row[5]),
            evidence_event_ids=parse_string_list(row[6]),
            evidence_summary=str(row[7]),
            metadata=parse_metadata(row[8]),
        )
        for row in rows
    )


def build_indexes(normalized: NormalizedSessionData) -> DiagnosticIndexes:
    records: defaultdict[int, list] = defaultdict(list)
    for event in normalized.raw_events:
        records[event.record_index].append(event)
    return DiagnosticIndexes(
        raw_events_by_id=immutable_index(
            {event.event_id: event for event in normalized.raw_events}
        ),
        raw_events_by_record_index=immutable_record_index(
            {index: tuple(events) for index, events in records.items()}
        ),
        messages_by_id=immutable_index({row.message_id: row for row in normalized.messages}),
        tool_calls_by_id=immutable_index({row.tool_call_id: row for row in normalized.tool_calls}),
        tool_results_by_id=immutable_index(
            {row.tool_result_id: row for row in normalized.tool_results}
        ),
        command_runs_by_id=immutable_index(
            {row.command_run_id: row for row in normalized.command_runs}
        ),
        file_activities_by_id=immutable_index(
            {row.file_activity_id: row for row in normalized.file_activities}
        ),
    )


def find_unresolved_references(
    normalized: NormalizedSessionData,
    analysis: DiagnosticAnalysis,
    indexes: DiagnosticIndexes,
) -> UnresolvedDiagnosticReferences:
    raw_ids = indexes.raw_events_by_id
    message_ids = indexes.messages_by_id
    tool_call_ids = indexes.tool_calls_by_id
    return UnresolvedDiagnosticReferences(
        message_source_event_ids=missing_ids(
            (row.source_event_id for row in normalized.messages), raw_ids
        ),
        message_parent_ids=missing_ids(
            (row.parent_message_id for row in normalized.messages), message_ids
        ),
        tool_call_source_event_ids=missing_ids(
            (row.source_event_id for row in normalized.tool_calls), raw_ids
        ),
        tool_result_source_event_ids=missing_ids(
            (row.source_event_id for row in normalized.tool_results), raw_ids
        ),
        tool_result_tool_call_ids=missing_ids(
            (row.tool_call_id for row in normalized.tool_results), tool_call_ids
        ),
        command_source_event_ids=missing_ids(
            (row.source_event_id for row in normalized.command_runs), raw_ids
        ),
        command_tool_call_ids=missing_ids(
            (row.tool_call_id for row in normalized.command_runs), tool_call_ids
        ),
        file_source_event_ids=missing_ids(
            (row.source_event_id for row in normalized.file_activities), raw_ids
        ),
        warning_ids=tuple(
            warning.warning_id
            for warning in normalized.parse_warnings
            if warning.record_index is None
            or len(indexes.raw_events_by_record_index.get(warning.record_index, ())) != 1
        ),
        message_feature_message_ids=missing_ids(
            (row.message_id for row in analysis.message_features), message_ids
        ),
        message_feature_source_event_ids=missing_ids(
            (row.source_event_id for row in analysis.message_features), raw_ids
        ),
        classification_source_event_ids=missing_ids(
            (
                event_id
                for classification in analysis.classifications
                for event_id in classification.evidence_event_ids
            ),
            raw_ids,
        ),
    )


def missing_ids(values: Iterable[str | None], index: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(value for value in values if value is not None and value not in index)

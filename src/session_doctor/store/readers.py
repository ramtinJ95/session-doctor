from __future__ import annotations

from pathlib import Path

import duckdb

from session_doctor.adapters import ParsedSessionBundle

from .connection import read_connection
from .json_values import parse_metadata
from .migrations import TABLE_NAMES
from .models import SessionSummary, StoreInfo
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


def table_count(database_path: Path, table_name: str) -> int:
    if table_name not in TABLE_NAMES:
        msg = f"Unknown table: {table_name}"
        raise ValueError(msg)
    with read_connection(database_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0]) if row else 0


def list_session_summaries(database_path: Path) -> tuple[SessionSummary, ...]:
    if not database_path.exists():
        return ()
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT
                s.session_id,
                s.agent_name,
                CAST(s.started_at AS VARCHAR),
                CAST(s.ended_at AS VARCHAR),
                s.cwd,
                s.project_path,
                ss.source_path,
                COUNT(DISTINCT m.message_id) AS message_count,
                COUNT(DISTINCT c.command_run_id) AS command_count,
                COUNT(DISTINCT w.warning_id) AS warning_count
            FROM sessions s
            LEFT JOIN session_sources ss ON ss.source_id = s.source_id
            LEFT JOIN messages m ON m.session_id = s.session_id
            LEFT JOIN command_runs c ON c.session_id = s.session_id
            LEFT JOIN parse_warnings w ON w.source_id = s.source_id
            GROUP BY
                s.session_id,
                s.agent_name,
                s.started_at,
                s.ended_at,
                s.cwd,
                s.project_path,
                ss.source_path
            ORDER BY s.started_at NULLS LAST, s.session_id
            """
        ).fetchall()
        message_source_counts = message_source_counts_by_session(connection)

    summaries: list[SessionSummary] = []
    for row in rows:
        session_counts = message_source_counts.get(row[0], {})
        summaries.append(
            SessionSummary(
                session_id=str(row[0]),
                agent_name=str(row[1]),
                started_at=row[2],
                ended_at=row[3],
                cwd=row[4],
                project_path=row[5],
                source_path=row[6],
                message_count=int(row[7]),
                response_item_message_count=session_counts.get("response_item", 0),
                event_msg_fallback_count=session_counts.get("event_msg_fallback", 0),
                command_count=int(row[8]),
                warning_count=int(row[9]),
            )
        )
    return tuple(summaries)


def load_session_bundle(database_path: Path, session_id: str) -> ParsedSessionBundle | None:
    if not database_path.exists():
        return None
    with read_connection(database_path) as connection:
        session = load_session(connection, session_id)
        if session is None:
            return None
        return ParsedSessionBundle(
            session=session,
            raw_events=load_raw_events(connection, session.source_id),
            messages=load_messages(connection, session_id),
            tool_calls=load_tool_calls(connection, session_id),
            tool_results=load_tool_results(connection, session_id),
            command_runs=load_command_runs(connection, session_id),
            file_activities=load_file_activities(connection, session_id),
            model_usage=load_model_usage(connection, session_id),
            parse_warnings=load_parse_warnings(connection, session.source_id),
        )


def store_info(database_path: Path) -> StoreInfo:
    if not database_path.exists():
        return StoreInfo(
            database_path=database_path,
            exists=False,
            schema_version=None,
            tables=(),
        )

    with read_connection(database_path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                ORDER BY table_name
                """
            ).fetchall()
        )
        schema_version = latest_schema_version(connection, tables)

    return StoreInfo(
        database_path=database_path,
        exists=True,
        schema_version=schema_version,
        tables=tables,
    )


def latest_schema_version(
    connection: duckdb.DuckDBPyConnection,
    tables: tuple[str, ...],
) -> int | None:
    if "schema_migrations" not in tables:
        return None
    row = connection.execute(
        "SELECT MAX(version) FROM schema_migrations",
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def message_source_counts_by_session(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, dict[str, int]]:
    rows = connection.execute(
        "SELECT session_id, metadata_json FROM messages",
    ).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for session_id, metadata_payload in rows:
        metadata = parse_metadata(metadata_payload)
        source = metadata.get("codex_message_source")
        if not isinstance(source, str):
            continue
        session_counts = counts.setdefault(str(session_id), {})
        session_counts[source] = session_counts.get(source, 0) + 1
    return counts

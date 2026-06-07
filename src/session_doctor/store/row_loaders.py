from __future__ import annotations

import duckdb

from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    ModelUsage,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    ToolCall,
    ToolResult,
)

from .json_values import parse_metadata, parse_string_list


def load_session(connection: duckdb.DuckDBPyConnection, session_id: str) -> Session | None:
    row = connection.execute(
        """
        SELECT
            session_id,
            source_id,
            agent_name,
            native_session_id,
            parent_session_id,
            started_at,
            ended_at,
            cwd,
            project_path,
            agent_version,
            model_provider,
            model,
            is_sidechain,
            metadata_json
        FROM sessions
        WHERE session_id = ?
        """,
        [session_id],
    ).fetchone()
    if row is None:
        return None
    return Session(
        session_id=row[0],
        source_id=row[1],
        agent_name=AgentName(row[2]),
        native_session_id=row[3],
        parent_session_id=row[4],
        started_at=row[5],
        ended_at=row[6],
        cwd=row[7],
        project_path=row[8],
        agent_version=row[9],
        model_provider=row[10],
        model=row[11],
        is_sidechain=bool(row[12]),
        metadata=parse_metadata(row[13]),
    )


def load_raw_events(connection: duckdb.DuckDBPyConnection, source_id: str) -> list[RawEvent]:
    rows = connection.execute(
        """
        SELECT
            event_id,
            source_id,
            agent_name,
            record_index,
            native_event_type,
            native_event_id,
            native_parent_id,
            timestamp,
            payload_hash,
            metadata_json
        FROM raw_events
        WHERE source_id = ?
        ORDER BY record_index, event_id
        """,
        [source_id],
    ).fetchall()
    return [
        RawEvent(
            event_id=row[0],
            source_id=row[1],
            agent_name=AgentName(row[2]),
            record_index=row[3],
            native_event_type=row[4],
            native_event_id=row[5],
            native_parent_id=row[6],
            timestamp=row[7],
            payload_hash=row[8],
            metadata=parse_metadata(row[9]),
        )
        for row in rows
    ]


def load_messages(connection: duckdb.DuckDBPyConnection, session_id: str) -> list[Message]:
    rows = connection.execute(
        """
        SELECT
            m.message_id,
            m.session_id,
            m.role,
            m.source_event_id,
            m.native_message_id,
            m.parent_message_id,
            m.timestamp,
            m.text,
            m.text_hash,
            m.text_length,
            m.content_block_types_json,
            m.metadata_json
        FROM messages AS m
        LEFT JOIN raw_events AS e ON e.event_id = m.source_event_id
        WHERE m.session_id = ?
        ORDER BY e.record_index NULLS LAST, m.timestamp NULLS LAST, m.message_id
        """,
        [session_id],
    ).fetchall()
    return [
        Message(
            message_id=row[0],
            session_id=row[1],
            role=NormalizedRole(row[2]),
            source_event_id=row[3],
            native_message_id=row[4],
            parent_message_id=row[5],
            timestamp=row[6],
            text=row[7],
            text_hash=row[8],
            text_length=row[9],
            content_block_types=parse_string_list(row[10]),
            metadata=parse_metadata(row[11]),
        )
        for row in rows
    ]


def load_tool_calls(connection: duckdb.DuckDBPyConnection, session_id: str) -> list[ToolCall]:
    rows = connection.execute(
        """
        SELECT
            t.tool_call_id,
            t.session_id,
            t.source_event_id,
            t.native_tool_call_id,
            t.name,
            t.timestamp,
            t.arguments_hash,
            t.metadata_json
        FROM tool_calls AS t
        LEFT JOIN raw_events AS e ON e.event_id = t.source_event_id
        WHERE t.session_id = ?
        ORDER BY e.record_index NULLS LAST, t.timestamp NULLS LAST, t.tool_call_id
        """,
        [session_id],
    ).fetchall()
    return [
        ToolCall(
            tool_call_id=row[0],
            session_id=row[1],
            source_event_id=row[2],
            native_tool_call_id=row[3],
            name=row[4],
            timestamp=row[5],
            arguments_hash=row[6],
            metadata=parse_metadata(row[7]),
        )
        for row in rows
    ]


def load_tool_results(connection: duckdb.DuckDBPyConnection, session_id: str) -> list[ToolResult]:
    rows = connection.execute(
        """
        SELECT
            t.tool_result_id,
            t.session_id,
            t.tool_call_id,
            t.source_event_id,
            t.native_tool_call_id,
            t.timestamp,
            t.is_error,
            t.output_hash,
            t.output_length,
            t.metadata_json
        FROM tool_results AS t
        LEFT JOIN raw_events AS e ON e.event_id = t.source_event_id
        WHERE t.session_id = ?
        ORDER BY e.record_index NULLS LAST, t.timestamp NULLS LAST, t.tool_result_id
        """,
        [session_id],
    ).fetchall()
    return [
        ToolResult(
            tool_result_id=row[0],
            session_id=row[1],
            tool_call_id=row[2],
            source_event_id=row[3],
            native_tool_call_id=row[4],
            timestamp=row[5],
            is_error=row[6],
            output_hash=row[7],
            output_length=row[8],
            metadata=parse_metadata(row[9]),
        )
        for row in rows
    ]


def load_command_runs(connection: duckdb.DuckDBPyConnection, session_id: str) -> list[CommandRun]:
    rows = connection.execute(
        """
        SELECT
            c.command_run_id,
            c.session_id,
            c.source_event_id,
            c.tool_call_id,
            c.command,
            c.cwd,
            c.started_at,
            c.ended_at,
            c.exit_code,
            c.stdout_hash,
            c.stderr_hash,
            c.output_length,
            c.metadata_json
        FROM command_runs AS c
        LEFT JOIN raw_events AS e ON e.event_id = c.source_event_id
        WHERE c.session_id = ?
        ORDER BY e.record_index NULLS LAST,
            c.ended_at NULLS LAST,
            c.started_at NULLS LAST,
            c.command_run_id
        """,
        [session_id],
    ).fetchall()
    return [
        CommandRun(
            command_run_id=row[0],
            session_id=row[1],
            source_event_id=row[2],
            tool_call_id=row[3],
            command=row[4],
            cwd=row[5],
            started_at=row[6],
            ended_at=row[7],
            exit_code=row[8],
            stdout_hash=row[9],
            stderr_hash=row[10],
            output_length=row[11],
            metadata=parse_metadata(row[12]),
        )
        for row in rows
    ]


def load_file_activities(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
) -> list[FileActivity]:
    rows = connection.execute(
        """
        SELECT
            f.file_activity_id,
            f.session_id,
            f.source_event_id,
            f.path,
            f.operation,
            f.timestamp,
            f.content_hash,
            f.metadata_json
        FROM file_activities AS f
        LEFT JOIN raw_events AS e ON e.event_id = f.source_event_id
        WHERE f.session_id = ?
        ORDER BY e.record_index NULLS LAST, f.timestamp NULLS LAST, f.file_activity_id
        """,
        [session_id],
    ).fetchall()
    return [
        FileActivity(
            file_activity_id=row[0],
            session_id=row[1],
            source_event_id=row[2],
            path=row[3],
            operation=row[4],
            timestamp=row[5],
            content_hash=row[6],
            metadata=parse_metadata(row[7]),
        )
        for row in rows
    ]


def load_model_usage(connection: duckdb.DuckDBPyConnection, session_id: str) -> list[ModelUsage]:
    rows = connection.execute(
        """
        SELECT
            u.model_usage_id,
            u.session_id,
            u.source_event_id,
            u.timestamp,
            u.provider,
            u.model,
            u.input_tokens,
            u.output_tokens,
            u.cache_read_tokens,
            u.cache_write_tokens,
            u.total_tokens,
            u.cost,
            u.metadata_json
        FROM model_usage AS u
        LEFT JOIN raw_events AS e ON e.event_id = u.source_event_id
        WHERE u.session_id = ?
        ORDER BY e.record_index NULLS LAST, u.timestamp NULLS LAST, u.model_usage_id
        """,
        [session_id],
    ).fetchall()
    return [
        ModelUsage(
            model_usage_id=row[0],
            session_id=row[1],
            source_event_id=row[2],
            timestamp=row[3],
            provider=row[4],
            model=row[5],
            input_tokens=row[6],
            output_tokens=row[7],
            cache_read_tokens=row[8],
            cache_write_tokens=row[9],
            total_tokens=row[10],
            cost=row[11],
            metadata=parse_metadata(row[12]),
        )
        for row in rows
    ]


def load_parse_warnings(
    connection: duckdb.DuckDBPyConnection,
    source_id: str,
) -> list[ParseWarning]:
    rows = connection.execute(
        """
        SELECT
            warning_id,
            source_id,
            record_index,
            severity,
            message,
            metadata_json
        FROM parse_warnings
        WHERE source_id = ?
        ORDER BY record_index NULLS LAST, warning_id
        """,
        [source_id],
    ).fetchall()
    return [
        ParseWarning(
            warning_id=row[0],
            source_id=row[1],
            record_index=row[2],
            severity=row[3],
            message=row[4],
            metadata=parse_metadata(row[5]),
        )
        for row in rows
    ]

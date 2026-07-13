from __future__ import annotations

import duckdb

SCHEMA_VERSION = 5

DURABLE_TABLE_NAMES = (
    "source_blobs",
    "logical_sources",
    "source_snapshots",
    "snapshot_bundles",
    "snapshot_bundle_members",
)

DERIVED_TABLE_NAMES = (
    "session_sources",
    "sessions",
    "raw_events",
    "messages",
    "tool_calls",
    "tool_results",
    "command_runs",
    "file_activities",
    "model_usage",
    "parse_warnings",
    "analysis_runs",
    "message_features",
    "session_features",
    "session_classifications",
)

TABLE_NAMES = ("schema_migrations", *DURABLE_TABLE_NAMES, *DERIVED_TABLE_NAMES)


class SchemaMismatchError(RuntimeError):
    def __init__(
        self,
        actual_version: int | None,
        *,
        missing_tables: tuple[str, ...] = (),
    ) -> None:
        self.actual_version = actual_version
        self.missing_tables = missing_tables
        actual = "missing" if actual_version is None else str(actual_version)
        detail = f"database schema version is {actual}; expected {SCHEMA_VERSION}"
        if missing_tables:
            detail = f"{detail}; missing tables: {', '.join(missing_tables)}"
        super().__init__(f"{detail}. Rebuild the database.")


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    existing_tables = database_tables(connection)
    if existing_tables:
        actual_version = database_schema_version(connection, existing_tables)
        if (
            actual_version is not None
            and actual_version < SCHEMA_VERSION
            and set(DURABLE_TABLE_NAMES).issubset(existing_tables)
        ):
            rebuild_derived_schema(connection)
            return
        require_current_schema(connection)
        return

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )

    for statement in CREATE_TABLE_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        [SCHEMA_VERSION],
    )


def rebuild_derived_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("BEGIN TRANSACTION")
    try:
        for table_name in reversed(DERIVED_TABLE_NAMES):
            connection.execute(f"DROP TABLE IF EXISTS {table_name}")
        for statement in CREATE_TABLE_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
            [SCHEMA_VERSION],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def require_current_schema(
    connection: duckdb.DuckDBPyConnection,
    *,
    allow_empty: bool = False,
) -> None:
    tables = database_tables(connection)
    if allow_empty and not tables:
        return
    actual_version = database_schema_version(connection, tables)
    missing_tables = tuple(sorted(set(TABLE_NAMES) - set(tables)))
    if actual_version != SCHEMA_VERSION or missing_tables:
        raise SchemaMismatchError(actual_version, missing_tables=missing_tables)


def database_tables(connection: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
            """
        ).fetchall()
    )


def database_schema_version(
    connection: duckdb.DuckDBPyConnection,
    tables: tuple[str, ...] | None = None,
) -> int | None:
    known_tables = tables if tables is not None else database_tables(connection)
    if "schema_migrations" not in known_tables:
        return None
    row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else None


CREATE_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS source_blobs (
        blob_id VARCHAR PRIMARY KEY,
        content_hash VARCHAR NOT NULL UNIQUE,
        codec VARCHAR NOT NULL CHECK (codec IN ('zlib')),
        compressed_bytes BLOB NOT NULL,
        original_byte_length BIGINT NOT NULL CHECK (original_byte_length >= 0),
        created_at TIMESTAMP NOT NULL DEFAULT current_timestamp
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logical_sources (
        logical_source_id VARCHAR PRIMARY KEY,
        agent_name VARCHAR NOT NULL,
        source_kind VARCHAR NOT NULL,
        source_path VARCHAR NOT NULL,
        first_seen_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        agent_name VARCHAR NOT NULL,
        source_kind VARCHAR NOT NULL,
        source_path VARCHAR NOT NULL,
        native_session_id VARCHAR,
        parent_source_id VARCHAR,
        source_metadata_json VARCHAR NOT NULL DEFAULT '{}',
        logical_source_id VARCHAR NOT NULL REFERENCES logical_sources(logical_source_id),
        blob_id VARCHAR NOT NULL REFERENCES source_blobs(blob_id),
        snapshot_content_id VARCHAR NOT NULL,
        capture_sequence BIGINT NOT NULL CHECK (capture_sequence > 0),
        captured_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        native_modified_at TIMESTAMP,
        capture_status VARCHAR NOT NULL CHECK (capture_status IN ('captured')),
        previous_snapshot_id VARCHAR REFERENCES source_snapshots(snapshot_id),
        UNIQUE (logical_source_id, capture_sequence),
        UNIQUE (snapshot_id, logical_source_id),
        UNIQUE (snapshot_id, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot_bundles (
        snapshot_bundle_id VARCHAR PRIMARY KEY,
        bundle_content_id VARCHAR NOT NULL,
        agent_name VARCHAR NOT NULL,
        native_session_identity VARCHAR NOT NULL,
        native_identity_status VARCHAR NOT NULL
            CHECK (native_identity_status IN ('observed', 'fallback_parse_failed')),
        native_bundle_capture_sequence BIGINT NOT NULL
            CHECK (native_bundle_capture_sequence > 0),
        previous_snapshot_bundle_id VARCHAR REFERENCES snapshot_bundles(snapshot_bundle_id),
        captured_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        UNIQUE (agent_name, native_session_identity, native_bundle_capture_sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot_bundle_members (
        snapshot_bundle_id VARCHAR NOT NULL
            REFERENCES snapshot_bundles(snapshot_bundle_id),
        logical_source_id VARCHAR NOT NULL REFERENCES logical_sources(logical_source_id),
        snapshot_id VARCHAR NOT NULL,
        capture_order INTEGER NOT NULL CHECK (capture_order >= 0),
        member_role VARCHAR NOT NULL,
        member_capture_status VARCHAR NOT NULL CHECK (member_capture_status IN ('captured')),
        PRIMARY KEY (snapshot_bundle_id, logical_source_id),
        UNIQUE (snapshot_bundle_id, capture_order),
        FOREIGN KEY (snapshot_id, logical_source_id)
            REFERENCES source_snapshots(snapshot_id, logical_source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_sources (
        source_id VARCHAR PRIMARY KEY,
        agent_name VARCHAR NOT NULL,
        source_path VARCHAR NOT NULL,
        source_kind VARCHAR NOT NULL,
        discovered_at TIMESTAMP,
        native_session_id VARCHAR,
        parent_source_id VARCHAR,
        snapshot_id VARCHAR REFERENCES source_snapshots(snapshot_id),
        snapshot_bundle_id VARCHAR REFERENCES snapshot_bundles(snapshot_bundle_id),
        CHECK ((snapshot_id IS NULL) = (snapshot_bundle_id IS NULL)),
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        agent_name VARCHAR NOT NULL,
        native_session_id VARCHAR,
        parent_session_id VARCHAR,
        started_at TIMESTAMP,
        ended_at TIMESTAMP,
        cwd VARCHAR,
        project_path VARCHAR,
        agent_version VARCHAR,
        model_provider VARCHAR,
        model VARCHAR,
        is_sidechain BOOLEAN NOT NULL DEFAULT false,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_events (
        event_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        agent_name VARCHAR NOT NULL,
        record_index INTEGER NOT NULL,
        native_event_type VARCHAR,
        native_event_id VARCHAR,
        native_parent_id VARCHAR,
        timestamp TIMESTAMP,
        payload_hash VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        role VARCHAR NOT NULL,
        source_event_id VARCHAR,
        native_message_id VARCHAR,
        parent_message_id VARCHAR,
        timestamp TIMESTAMP,
        text VARCHAR,
        text_hash VARCHAR,
        text_length INTEGER NOT NULL DEFAULT 0,
        content_block_types_json VARCHAR NOT NULL DEFAULT '[]',
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        tool_call_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        native_tool_call_id VARCHAR,
        name VARCHAR NOT NULL,
        timestamp TIMESTAMP,
        arguments_hash VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_results (
        tool_result_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        tool_call_id VARCHAR,
        source_event_id VARCHAR,
        native_tool_call_id VARCHAR,
        timestamp TIMESTAMP,
        is_error BOOLEAN,
        output_hash VARCHAR,
        output_length INTEGER,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS command_runs (
        command_run_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        tool_call_id VARCHAR,
        command VARCHAR NOT NULL,
        command_identity_hash VARCHAR NOT NULL,
        command_display VARCHAR NOT NULL,
        command_normalization VARCHAR NOT NULL,
        cwd VARCHAR,
        started_at TIMESTAMP,
        ended_at TIMESTAMP,
        exit_code INTEGER,
        stdout_hash VARCHAR,
        stderr_hash VARCHAR,
        output_length INTEGER,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS file_activities (
        file_activity_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        path VARCHAR NOT NULL,
        normalized_path VARCHAR NOT NULL,
        canonical_path VARCHAR,
        project_relative_path VARCHAR,
        path_resolution VARCHAR NOT NULL,
        operation VARCHAR NOT NULL,
        timestamp TIMESTAMP,
        content_hash VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_usage (
        model_usage_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        timestamp TIMESTAMP,
        provider VARCHAR,
        model VARCHAR,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cache_read_tokens INTEGER,
        cache_write_tokens INTEGER,
        total_tokens INTEGER,
        cost DECIMAL(18, 8),
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parse_warnings (
        warning_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        record_index INTEGER,
        severity VARCHAR NOT NULL,
        message VARCHAR NOT NULL,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        analysis_run_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        analyzer_version VARCHAR NOT NULL,
        artifact_path VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS message_features (
        message_feature_id VARCHAR PRIMARY KEY,
        analysis_run_id VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        message_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        feature_name VARCHAR NOT NULL,
        feature_value VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        evidence_json VARCHAR NOT NULL DEFAULT '{}',
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_features (
        session_feature_id VARCHAR PRIMARY KEY,
        analysis_run_id VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        feature_name VARCHAR NOT NULL,
        feature_value VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        evidence_json VARCHAR NOT NULL DEFAULT '{}',
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_classifications (
        session_classification_id VARCHAR PRIMARY KEY,
        analysis_run_id VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        label VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        confidence DOUBLE NOT NULL,
        evidence_event_ids_json VARCHAR NOT NULL DEFAULT '[]',
        evidence_summary VARCHAR NOT NULL,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
)

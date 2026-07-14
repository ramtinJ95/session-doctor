from __future__ import annotations

import json

import duckdb

from session_doctor.ids import stable_id

SCHEMA_VERSION = 11

BASE_DURABLE_TABLE_NAMES = (
    "source_blobs",
    "logical_sources",
    "source_snapshots",
    "snapshot_bundles",
    "snapshot_bundle_members",
)

DURABLE_TABLE_NAMES = (
    *BASE_DURABLE_TABLE_NAMES,
    "bundle_capture_metadata",
    "bundle_member_capture_metadata",
    "lifecycle_observations",
    "evaluation_packets",
    "evaluation_corpora",
    "judge_annotations",
    "judge_panel_resolutions",
    "audit_selections",
    "audit_protocols",
    "human_adjudications",
    "reference_resolutions",
)

DERIVED_TABLE_NAMES = (
    "normalization_runs",
    "normalization_run_bundles",
    "normalized_entities",
    "normalization_semantics",
    "semantic_analysis_runs",
    "episode_analysis_episodes",
    "episode_analysis_user_anchors",
    "episode_analysis_event_anchors",
    "episode_analysis_boundaries",
    "episode_boundary_evidence",
    "episode_episode_boundaries",
    "episode_analysis_observations",
    "episode_observation_evidence",
    "episode_projection_runs",
    "episode_projection_inputs",
    "episode_topology_candidates",
    "episode_topology_candidate_witnesses",
    "episode_delegation_bindings",
    "episode_delegation_binding_witnesses",
    "episode_delegations",
    "episode_entity_memberships",
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
)

LEGACY_V1_ANALYSIS_TABLE_NAMES = (
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
            and set(BASE_DURABLE_TABLE_NAMES).issubset(existing_tables)
        ):
            rebuild_derived_schema(connection, actual_version)
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


def rebuild_derived_schema(connection: duckdb.DuckDBPyConnection, actual_version: int) -> None:
    connection.execute("BEGIN TRANSACTION")
    try:
        for table_name in reversed((*DERIVED_TABLE_NAMES, *LEGACY_V1_ANALYSIS_TABLE_NAMES)):
            connection.execute(f"DROP TABLE IF EXISTS {table_name}")
        snapshot_rows: list[tuple[object, ...]] = []
        bundle_rows: list[tuple[object, ...]] = []
        member_rows: list[tuple[object, ...]] = []
        if actual_version < 6:
            snapshot_rows = connection.execute("SELECT * FROM source_snapshots").fetchall()
            bundle_rows = connection.execute("SELECT * FROM snapshot_bundles").fetchall()
            member_rows = connection.execute("SELECT * FROM snapshot_bundle_members").fetchall()
            connection.execute("DROP TABLE IF EXISTS lifecycle_observations")
            connection.execute("DROP TABLE IF EXISTS bundle_member_capture_metadata")
            connection.execute("DROP TABLE IF EXISTS bundle_capture_metadata")
            connection.execute("DROP TABLE snapshot_bundle_members")
            connection.execute("DROP TABLE snapshot_bundles")
            connection.execute("DROP TABLE source_snapshots")
        for statement in CREATE_TABLE_STATEMENTS:
            connection.execute(statement)
        if snapshot_rows:
            connection.executemany(
                "INSERT INTO source_snapshots VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                snapshot_rows,
            )
        if bundle_rows:
            connection.executemany(
                "INSERT INTO snapshot_bundles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                bundle_rows,
            )
        if member_rows:
            connection.executemany(
                "INSERT INTO snapshot_bundle_members VALUES (?, ?, ?, ?, ?, ?)",
                member_rows,
            )
        backfill_capture_history(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
            [SCHEMA_VERSION],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def backfill_capture_history(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        INSERT INTO bundle_member_capture_metadata (
            snapshot_bundle_id, capture_order, logical_source_id, snapshot_id,
            source_id, source_path, member_role, member_capture_status,
            capture_started_at, capture_completed_at,
            native_modified_before, native_modified_after, evidence_json
        )
        SELECT m.snapshot_bundle_id, m.capture_order, m.logical_source_id,
            m.snapshot_id, s.source_id, s.source_path, m.member_role,
            m.member_capture_status, s.captured_at, s.captured_at,
            s.native_modified_at, s.native_modified_at, ?
        FROM snapshot_bundle_members AS m
        JOIN source_snapshots AS s ON s.snapshot_id = m.snapshot_id
        LEFT JOIN bundle_member_capture_metadata AS existing
          ON existing.snapshot_bundle_id = m.snapshot_bundle_id
         AND existing.capture_order = m.capture_order
        WHERE existing.snapshot_bundle_id IS NULL
        """,
        [json.dumps({"migration": "schema-v5-to-v6"})],
    )
    rows = connection.execute(
        """
        SELECT b.snapshot_bundle_id, b.bundle_content_id, b.agent_name,
            b.native_session_identity, b.captured_at, s.logical_source_id,
            s.capture_sequence, b.native_bundle_capture_sequence,
            b.native_identity_status
        FROM snapshot_bundles AS b
        JOIN source_snapshots AS s ON s.snapshot_id = b.primary_snapshot_id
        LEFT JOIN bundle_capture_metadata AS c USING (snapshot_bundle_id)
        WHERE c.snapshot_bundle_id IS NULL
        ORDER BY s.logical_source_id, s.capture_sequence,
            b.native_bundle_capture_sequence, b.snapshot_bundle_id
        """
    ).fetchall()
    previous_by_lineage: dict[str, tuple[str, str, object, int, int]] = {}
    for (
        bundle_id,
        content_id,
        agent_name,
        native_identity,
        captured_at,
        logical_source_id,
        _source_capture_sequence,
        _bundle_capture_sequence,
        native_identity_status,
    ) in rows:
        lineage_id = stable_id("bundle-lineage", agent_name, native_identity, logical_source_id)
        previous = previous_by_lineage.get(lineage_id)
        sequence = previous[3] + 1 if previous else 1
        previous_bundle_id = previous[0] if previous else None
        capture_status = (
            "parse_failed" if native_identity_status == "fallback_parse_failed" else "incomplete"
        )
        connection.execute(
            """
            INSERT INTO bundle_capture_metadata (
                snapshot_bundle_id, lineage_id, lineage_capture_sequence,
                previous_lineage_bundle_id, capture_started_at,
                capture_completed_at, capture_status, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                bundle_id,
                lineage_id,
                sequence,
                previous_bundle_id,
                captured_at,
                captured_at,
                capture_status,
                json.dumps({"migration": "schema-v5-to-v6"}),
            ],
        )
        state = "snapshot_incomplete"
        observation_id = stable_id("lifecycle-observation", bundle_id, "lifecycle-v1", state)
        connection.execute(
            """
            INSERT INTO lifecycle_observations (
                lifecycle_observation_id, snapshot_bundle_id,
                lifecycle_policy_version, state, observed_at, evidence_json
            ) VALUES (?, ?, 'lifecycle-v1', ?, ?, ?)
            """,
            [
                observation_id,
                bundle_id,
                state,
                captured_at,
                json.dumps({"reason": "migrated_capture_history"}),
            ],
        )
        previous_by_lineage[lineage_id] = (
            str(bundle_id),
            str(content_id),
            captured_at,
            sequence,
            int(_source_capture_sequence),
        )


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
    CREATE TABLE IF NOT EXISTS normalization_runs (
        normalization_run_id VARCHAR PRIMARY KEY,
        bundle_content_id VARCHAR NOT NULL,
        adapter_name VARCHAR NOT NULL,
        adapter_version VARCHAR NOT NULL,
        normalization_version VARCHAR NOT NULL,
        configuration_hash VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        UNIQUE (
            bundle_content_id, adapter_name, adapter_version,
            normalization_version, configuration_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS normalization_run_bundles (
        normalization_run_id VARCHAR NOT NULL,
        snapshot_bundle_id VARCHAR NOT NULL,
        linked_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        PRIMARY KEY (normalization_run_id, snapshot_bundle_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS normalized_entities (
        normalization_run_id VARCHAR NOT NULL,
        entity_kind VARCHAR NOT NULL,
        entity_id VARCHAR NOT NULL,
        entity_order INTEGER NOT NULL CHECK (entity_order >= 0),
        payload_json VARCHAR NOT NULL,
        PRIMARY KEY (normalization_run_id, entity_kind, entity_id),
        UNIQUE (normalization_run_id, entity_kind, entity_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS normalization_semantics (
        normalization_run_id VARCHAR PRIMARY KEY,
        semantic_foundation_version VARCHAR NOT NULL,
        foundation_json VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_analysis_runs (
        analysis_identity VARCHAR PRIMARY KEY,
        normalization_run_id VARCHAR NOT NULL,
        lifecycle_observation_id VARCHAR NOT NULL,
        lifecycle_policy_version VARCHAR NOT NULL,
        ordering_version VARCHAR NOT NULL,
        segmentation_version VARCHAR NOT NULL,
        relation_rule_set_version VARCHAR NOT NULL,
        result_rule_set_version VARCHAR NOT NULL,
        finding_rule_set_version VARCHAR NOT NULL,
        facet_policy_version VARCHAR NOT NULL,
        configuration_hash VARCHAR NOT NULL,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_analysis_episodes (
        analysis_identity VARCHAR NOT NULL,
        episode_id VARCHAR NOT NULL,
        episode_order INTEGER NOT NULL CHECK (episode_order >= 0),
        segmentation_version VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        first_user_analysis_anchor_id VARCHAR NOT NULL,
        last_user_analysis_anchor_id VARCHAR NOT NULL,
        lifecycle_state VARCHAR NOT NULL,
        provisional BOOLEAN NOT NULL,
        PRIMARY KEY (analysis_identity, episode_id),
        UNIQUE (analysis_identity, episode_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_analysis_user_anchors (
        analysis_identity VARCHAR NOT NULL,
        episode_id VARCHAR NOT NULL,
        anchor_order INTEGER NOT NULL CHECK (anchor_order >= 0),
        anchor_id VARCHAR NOT NULL,
        anchor_kind VARCHAR NOT NULL CHECK (anchor_kind IN ('raw_event', 'message')),
        entity_id VARCHAR NOT NULL,
        payload_digest VARCHAR NOT NULL,
        PRIMARY KEY (analysis_identity, episode_id, anchor_order),
        UNIQUE (analysis_identity, episode_id, anchor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_analysis_event_anchors (
        analysis_identity VARCHAR NOT NULL,
        episode_id VARCHAR NOT NULL,
        anchor_order INTEGER NOT NULL CHECK (anchor_order >= 0),
        anchor_id VARCHAR NOT NULL,
        anchor_kind VARCHAR NOT NULL CHECK (anchor_kind IN ('raw_event', 'message')),
        entity_id VARCHAR NOT NULL,
        payload_digest VARCHAR NOT NULL,
        PRIMARY KEY (analysis_identity, episode_id, anchor_order),
        UNIQUE (analysis_identity, anchor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_analysis_boundaries (
        analysis_identity VARCHAR NOT NULL,
        boundary_id VARCHAR NOT NULL,
        boundary_order INTEGER NOT NULL CHECK (boundary_order >= 0),
        left_user_analysis_anchor_id VARCHAR NOT NULL,
        right_user_analysis_anchor_id VARCHAR NOT NULL,
        decision VARCHAR NOT NULL CHECK (decision IN ('split', 'no_split', 'ambiguous')),
        reason VARCHAR NOT NULL,
        broad_goal_similarity DOUBLE CHECK (
            broad_goal_similarity IS NULL OR
            (broad_goal_similarity >= 0 AND broad_goal_similarity <= 1)
        ),
        PRIMARY KEY (analysis_identity, boundary_id),
        UNIQUE (analysis_identity, boundary_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_boundary_evidence (
        analysis_identity VARCHAR NOT NULL,
        boundary_id VARCHAR NOT NULL,
        evidence_order INTEGER NOT NULL CHECK (evidence_order >= 0),
        evidence_anchor_id VARCHAR NOT NULL,
        anchor_kind VARCHAR NOT NULL CHECK (anchor_kind IN ('raw_event', 'message')),
        entity_id VARCHAR NOT NULL,
        payload_digest VARCHAR NOT NULL,
        PRIMARY KEY (analysis_identity, boundary_id, evidence_order),
        UNIQUE (analysis_identity, boundary_id, evidence_anchor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_episode_boundaries (
        analysis_identity VARCHAR NOT NULL,
        episode_id VARCHAR NOT NULL,
        boundary_order INTEGER NOT NULL CHECK (boundary_order >= 0),
        boundary_id VARCHAR NOT NULL,
        PRIMARY KEY (analysis_identity, episode_id, boundary_order),
        UNIQUE (analysis_identity, episode_id, boundary_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_analysis_observations (
        analysis_identity VARCHAR NOT NULL,
        observation_id VARCHAR NOT NULL,
        episode_id VARCHAR NOT NULL,
        observation_kind VARCHAR NOT NULL,
        observation_order INTEGER NOT NULL CHECK (observation_order >= 0),
        PRIMARY KEY (analysis_identity, observation_id),
        UNIQUE (analysis_identity, episode_id, observation_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_observation_evidence (
        analysis_identity VARCHAR NOT NULL,
        observation_id VARCHAR NOT NULL,
        evidence_order INTEGER NOT NULL CHECK (evidence_order >= 0),
        evidence_anchor_id VARCHAR NOT NULL,
        anchor_kind VARCHAR NOT NULL CHECK (anchor_kind IN ('raw_event', 'message')),
        entity_id VARCHAR NOT NULL,
        payload_digest VARCHAR NOT NULL,
        PRIMARY KEY (analysis_identity, observation_id, evidence_order),
        UNIQUE (analysis_identity, observation_id, evidence_anchor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_projection_runs (
        episode_projection_id VARCHAR PRIMARY KEY,
        requested_analysis_identity VARCHAR NOT NULL,
        requested_session_id VARCHAR NOT NULL,
        topology_policy_version VARCHAR NOT NULL,
        configuration_hash VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT current_timestamp
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_projection_inputs (
        episode_projection_id VARCHAR NOT NULL,
        analysis_identity VARCHAR NOT NULL,
        input_order INTEGER NOT NULL CHECK (input_order >= 0),
        discovery_role VARCHAR NOT NULL CHECK (
            discovery_role IN ('requested', 'ancestor', 'descendant', 'candidate')
        ),
        session_id VARCHAR NOT NULL,
        normalization_run_id VARCHAR NOT NULL,
        snapshot_bundle_id VARCHAR NOT NULL,
        lifecycle_observation_id VARCHAR NOT NULL,
        PRIMARY KEY (episode_projection_id, analysis_identity),
        UNIQUE (episode_projection_id, input_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_topology_candidates (
        episode_projection_id VARCHAR NOT NULL,
        topology_candidate_id VARCHAR NOT NULL,
        direction VARCHAR NOT NULL CHECK (direction IN ('parent', 'child')),
        native_spawn_identity VARCHAR,
        parent_source_id VARCHAR,
        parent_logical_source_id VARCHAR,
        parent_snapshot_content_id VARCHAR,
        child_source_id VARCHAR,
        child_logical_source_id VARCHAR,
        child_snapshot_content_id VARCHAR,
        parent_analysis_identity VARCHAR,
        child_analysis_identity VARCHAR,
        status VARCHAR NOT NULL CHECK (
            status IN ('linked', 'unavailable', 'ambiguous', 'not_child')
        ),
        reason VARCHAR NOT NULL,
        endpoint_status VARCHAR NOT NULL CHECK (
            endpoint_status IN ('observed', 'missing', 'unavailable')
        ),
        CHECK (
            endpoint_status = 'observed' OR
            parent_analysis_identity IS NULL OR child_analysis_identity IS NULL
        ),
        PRIMARY KEY (episode_projection_id, topology_candidate_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_topology_candidate_witnesses (
        episode_projection_id VARCHAR NOT NULL,
        topology_candidate_id VARCHAR NOT NULL,
        witness_bundle_id VARCHAR NOT NULL,
        parent_member_snapshot_id VARCHAR,
        child_member_snapshot_id VARCHAR,
        spawn_entity_kind VARCHAR,
        spawn_entity_id VARCHAR,
        spawn_anchor_id VARCHAR,
        PRIMARY KEY (episode_projection_id, topology_candidate_id, witness_bundle_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_delegation_bindings (
        episode_projection_id VARCHAR NOT NULL,
        child_analysis_identity VARCHAR NOT NULL,
        parent_analysis_identity VARCHAR NOT NULL,
        parent_episode_id VARCHAR NOT NULL,
        spawn_entity_kind VARCHAR NOT NULL,
        spawn_entity_id VARCHAR NOT NULL,
        spawn_anchor_id VARCHAR NOT NULL,
        topology_policy_version VARCHAR NOT NULL,
        provenance_json VARCHAR NOT NULL,
        PRIMARY KEY (episode_projection_id, child_analysis_identity)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_delegation_binding_witnesses (
        episode_projection_id VARCHAR NOT NULL,
        child_analysis_identity VARCHAR NOT NULL,
        witness_bundle_id VARCHAR NOT NULL,
        topology_candidate_id VARCHAR NOT NULL,
        parent_member_snapshot_id VARCHAR NOT NULL,
        child_member_snapshot_id VARCHAR NOT NULL,
        spawn_entity_kind VARCHAR NOT NULL,
        spawn_entity_id VARCHAR NOT NULL,
        spawn_anchor_id VARCHAR NOT NULL,
        PRIMARY KEY (episode_projection_id, child_analysis_identity, witness_bundle_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_delegations (
        episode_projection_id VARCHAR NOT NULL,
        child_analysis_identity VARCHAR NOT NULL,
        child_episode_id VARCHAR NOT NULL,
        parent_analysis_identity VARCHAR NOT NULL,
        parent_episode_id VARCHAR NOT NULL,
        delegation_id VARCHAR NOT NULL,
        PRIMARY KEY (episode_projection_id, child_analysis_identity, child_episode_id),
        UNIQUE (episode_projection_id, delegation_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episode_entity_memberships (
        episode_projection_id VARCHAR NOT NULL,
        source_analysis_identity VARCHAR NOT NULL,
        entity_kind VARCHAR NOT NULL,
        entity_id VARCHAR NOT NULL,
        normalization_run_id VARCHAR NOT NULL,
        entity_order INTEGER NOT NULL CHECK (entity_order >= 0),
        membership_status VARCHAR NOT NULL CHECK (
            membership_status IN ('assigned', 'ambiguous', 'unassigned')
        ),
        source_episode_id VARCHAR,
        rollup_owner_status VARCHAR NOT NULL CHECK (
            rollup_owner_status IN ('known', 'unavailable')
        ),
        rollup_owner_analysis_identity VARCHAR,
        rollup_owner_episode_id VARCHAR,
        aggregate_eligibility VARCHAR NOT NULL CHECK (
            aggregate_eligibility IN ('direct', 'excluded_delegated', 'ineligible')
        ),
        reason VARCHAR NOT NULL,
        candidate_episode_keys_json VARCHAR NOT NULL,
        CHECK (
            (membership_status = 'assigned' AND source_episode_id IS NOT NULL) OR
            (membership_status != 'assigned' AND source_episode_id IS NULL)
        ),
        CHECK (
            (rollup_owner_status = 'known' AND
                rollup_owner_analysis_identity IS NOT NULL AND
                rollup_owner_episode_id IS NOT NULL) OR
            (rollup_owner_status = 'unavailable' AND
                rollup_owner_analysis_identity IS NULL AND
                rollup_owner_episode_id IS NULL)
        ),
        CHECK (
            (membership_status = 'assigned' AND candidate_episode_keys_json = '[]') OR
            membership_status != 'assigned'
        ),
        PRIMARY KEY (
            episode_projection_id, source_analysis_identity, entity_kind, entity_id
        ),
        UNIQUE (
            episode_projection_id, source_analysis_identity, entity_kind, entity_order
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluation_packets (
        packet_id VARCHAR PRIMARY KEY,
        schema_version VARCHAR NOT NULL,
        annotation_protocol_version VARCHAR NOT NULL,
        packet_kind VARCHAR NOT NULL,
        evaluation_corpus_id VARCHAR NOT NULL,
        normalization_run_id VARCHAR,
        snapshot_bundle_id VARCHAR NOT NULL,
        routing_json VARCHAR NOT NULL,
        judge_packet_json VARCHAR NOT NULL,
        judge_packet_hash VARCHAR NOT NULL,
        evidence_ids_json VARCHAR NOT NULL,
        allowed_answers_json VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT current_timestamp
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluation_corpora (
        evaluation_corpus_id VARCHAR PRIMARY KEY,
        annotation_protocol_version VARCHAR NOT NULL,
        expected_packet_count INTEGER NOT NULL CHECK (expected_packet_count > 0),
        source_identity VARCHAR NOT NULL,
        registered_at TIMESTAMP NOT NULL DEFAULT current_timestamp
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS judge_annotations (
        judge_annotation_id VARCHAR PRIMARY KEY,
        schema_version VARCHAR NOT NULL,
        annotation_protocol_version VARCHAR NOT NULL,
        packet_id VARCHAR NOT NULL,
        judge_model VARCHAR NOT NULL,
        judge_provider VARCHAR NOT NULL,
        judge_prompt_version VARCHAR NOT NULL,
        answer VARCHAR NOT NULL,
        evidence_ids_json VARCHAR NOT NULL,
        rationale VARCHAR NOT NULL,
        created_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS judge_panel_resolutions (
        judge_panel_resolution_id VARCHAR PRIMARY KEY,
        schema_version VARCHAR NOT NULL,
        annotation_protocol_version VARCHAR NOT NULL,
        packet_id VARCHAR NOT NULL,
        judge_annotation_ids_json VARCHAR NOT NULL,
        consensus_status VARCHAR NOT NULL,
        unanimous_answer VARCHAR,
        resolved_at TIMESTAMP NOT NULL,
        UNIQUE (packet_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_selections (
        audit_selection_id VARCHAR PRIMARY KEY,
        schema_version VARCHAR NOT NULL,
        annotation_protocol_version VARCHAR NOT NULL,
        packet_id VARCHAR NOT NULL,
        judge_panel_resolution_id VARCHAR NOT NULL UNIQUE,
        eligibility_status VARCHAR NOT NULL,
        selection_status VARCHAR NOT NULL,
        selection_seed_id VARCHAR NOT NULL,
        selection_reason VARCHAR NOT NULL,
        selected_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_protocols (
        audit_protocol_id VARCHAR PRIMARY KEY,
        annotation_protocol_version VARCHAR NOT NULL,
        evaluation_corpus_id VARCHAR NOT NULL,
        expected_packet_count INTEGER NOT NULL CHECK (expected_packet_count > 0),
        selection_seed_id VARCHAR NOT NULL,
        cohort_packet_ids_json VARCHAR NOT NULL,
        eligible_packet_ids_json VARCHAR NOT NULL,
        selected_packet_ids_json VARCHAR NOT NULL,
        frozen_at TIMESTAMP NOT NULL,
        UNIQUE (annotation_protocol_version, evaluation_corpus_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS human_adjudications (
        human_adjudication_id VARCHAR PRIMARY KEY,
        schema_version VARCHAR NOT NULL,
        annotation_protocol_version VARCHAR NOT NULL,
        packet_id VARCHAR NOT NULL,
        judge_panel_resolution_id VARCHAR NOT NULL,
        audit_selection_id VARCHAR,
        review_kind VARCHAR NOT NULL,
        reviewer_identity VARCHAR NOT NULL,
        answer VARCHAR NOT NULL,
        evidence_ids_json VARCHAR NOT NULL,
        rationale VARCHAR NOT NULL,
        reviewed_at TIMESTAMP NOT NULL,
        UNIQUE (judge_panel_resolution_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_resolutions (
        reference_resolution_id VARCHAR PRIMARY KEY,
        schema_version VARCHAR NOT NULL,
        annotation_protocol_version VARCHAR NOT NULL,
        packet_id VARCHAR NOT NULL,
        resolution_status VARCHAR NOT NULL,
        answer VARCHAR NOT NULL,
        source_judge_panel_resolution_id VARCHAR NOT NULL,
        source_audit_selection_id VARCHAR,
        source_human_adjudication_id VARCHAR,
        resolved_at TIMESTAMP NOT NULL,
        UNIQUE (source_judge_panel_resolution_id)
    )
    """,
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
        discovered_at VARCHAR,
        native_session_id VARCHAR,
        parent_source_id VARCHAR,
        source_metadata_json VARCHAR NOT NULL DEFAULT '{}',
        logical_source_id VARCHAR NOT NULL,
        blob_id VARCHAR NOT NULL,
        snapshot_content_id VARCHAR NOT NULL,
        capture_sequence BIGINT NOT NULL CHECK (capture_sequence > 0),
        captured_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        native_modified_at TIMESTAMP,
        capture_status VARCHAR NOT NULL CHECK (capture_status IN ('captured')),
        previous_snapshot_id VARCHAR,
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
        primary_snapshot_id VARCHAR NOT NULL,
        native_identity_status VARCHAR NOT NULL
            CHECK (native_identity_status IN ('observed', 'fallback_parse_failed')),
        native_bundle_capture_sequence BIGINT NOT NULL
            CHECK (native_bundle_capture_sequence > 0),
        previous_snapshot_bundle_id VARCHAR,
        captured_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
        UNIQUE (agent_name, native_session_identity, native_bundle_capture_sequence),
        UNIQUE (primary_snapshot_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot_bundle_members (
        snapshot_bundle_id VARCHAR NOT NULL,
        logical_source_id VARCHAR NOT NULL,
        snapshot_id VARCHAR NOT NULL,
        capture_order INTEGER NOT NULL CHECK (capture_order >= 0),
        member_role VARCHAR NOT NULL,
        member_capture_status VARCHAR NOT NULL CHECK (
            member_capture_status IN ('captured', 'changed_during_capture')
        ),
        PRIMARY KEY (snapshot_bundle_id, logical_source_id),
        UNIQUE (snapshot_bundle_id, capture_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bundle_capture_metadata (
        snapshot_bundle_id VARCHAR PRIMARY KEY,
        lineage_id VARCHAR NOT NULL,
        lineage_capture_sequence BIGINT NOT NULL CHECK (lineage_capture_sequence > 0),
        previous_lineage_bundle_id VARCHAR,
        capture_started_at TIMESTAMP NOT NULL,
        capture_completed_at TIMESTAMP NOT NULL,
        capture_status VARCHAR NOT NULL CHECK (
            capture_status IN ('complete', 'incomplete', 'skewed', 'parse_failed')
        ),
        evidence_json VARCHAR NOT NULL DEFAULT '{}',
        UNIQUE (lineage_id, lineage_capture_sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bundle_member_capture_metadata (
        snapshot_bundle_id VARCHAR NOT NULL,
        capture_order INTEGER NOT NULL CHECK (capture_order >= 0),
        logical_source_id VARCHAR,
        snapshot_id VARCHAR,
        source_id VARCHAR NOT NULL,
        source_path VARCHAR NOT NULL,
        member_role VARCHAR NOT NULL,
        member_capture_status VARCHAR NOT NULL CHECK (
            member_capture_status IN (
                'captured', 'missing', 'unreadable', 'changed_during_capture'
            )
        ),
        capture_started_at TIMESTAMP NOT NULL,
        capture_completed_at TIMESTAMP NOT NULL,
        native_modified_before TIMESTAMP,
        native_modified_after TIMESTAMP,
        evidence_json VARCHAR NOT NULL DEFAULT '{}',
        PRIMARY KEY (snapshot_bundle_id, capture_order),
        CHECK (
            (member_capture_status = 'captured' AND snapshot_id IS NOT NULL)
            OR member_capture_status != 'captured'
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lifecycle_observations (
        lifecycle_observation_id VARCHAR PRIMARY KEY,
        snapshot_bundle_id VARCHAR NOT NULL UNIQUE,
        lifecycle_policy_version VARCHAR NOT NULL,
        state VARCHAR NOT NULL CHECK (
            state IN (
                'terminal_observed', 'settled_unknown',
                'possibly_active', 'snapshot_incomplete'
            )
        ),
        observed_at TIMESTAMP NOT NULL,
        evidence_json VARCHAR NOT NULL DEFAULT '{}'
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
        snapshot_id VARCHAR,
        snapshot_bundle_id VARCHAR,
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
        aggregation_semantics VARCHAR NOT NULL,
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
)

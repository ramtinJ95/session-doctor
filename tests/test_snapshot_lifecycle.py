from __future__ import annotations

import hashlib
import zlib
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ingest_workflow import capture_status, topology_inputs_changed
from session_doctor.schemas import AgentName, Session, SessionSource
from session_doctor.store import BundleMemberCapture, DuckDBStore, SnapshotPruneBlocked


def source() -> SessionSource:
    return SessionSource(
        source_id="source-1",
        agent_name=AgentName.PI,
        source_path="/sessions/source-1.jsonl",
    )


def capture_bundle(
    store: DuckDBStore,
    source_row: SessionSource,
    source_bytes: bytes,
    captured_at: datetime,
    *,
    capture_status: str = "complete",
    terminal_observed: bool = False,
    native_identity: str = "native-session-1",
):
    captured = store.capture_source(source_row, source_bytes, captured_at=captured_at)
    bundle = store.create_single_source_bundle(
        source_row,
        captured,
        native_identity,
        capture_status=capture_status,
    )
    observation = store.record_lifecycle(
        bundle.snapshot_bundle_id,
        terminal_observed=terminal_observed,
    )
    return captured, bundle, observation


def test_lifecycle_settles_only_after_consecutive_identical_complete_capture(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    first, _, first_observation = capture_bundle(store, source(), b"A", started)
    second, _, second_observation = capture_bundle(
        store, source(), b"A", started + timedelta(seconds=31)
    )

    assert first_observation.state == "possibly_active"
    assert second_observation.state == "settled_unknown"
    assert first.capture_sequence == 1
    assert second.capture_sequence == 2


def test_lifecycle_terminal_evidence_outranks_first_capture(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    _, _, observation = capture_bundle(
        store,
        source(),
        b"terminal",
        datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        terminal_observed=True,
    )

    assert observation.state == "terminal_observed"


def test_lifecycle_does_not_settle_a_b_a_or_incomplete_sequence(tmp_path) -> None:
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    store = DuckDBStore(tmp_path / "a-b-a.duckdb")
    capture_bundle(store, source(), b"A", started)
    capture_bundle(store, source(), b"B", started + timedelta(seconds=31))
    _, _, third = capture_bundle(store, source(), b"A", started + timedelta(seconds=62))
    assert third.state == "possibly_active"

    incomplete_store = DuckDBStore(tmp_path / "incomplete.duckdb")
    capture_bundle(incomplete_store, source(), b"A", started)
    _, _, incomplete = capture_bundle(
        incomplete_store,
        source(),
        b"A",
        started + timedelta(seconds=31),
        capture_status="incomplete",
    )
    _, _, after_incomplete = capture_bundle(
        incomplete_store,
        source(),
        b"A",
        started + timedelta(seconds=62),
    )
    assert incomplete.state == "snapshot_incomplete"
    assert after_incomplete.state == "possibly_active"


def test_capture_sequence_not_timestamp_controls_predecessor(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    first, _, _ = capture_bundle(store, source(), b"A", started)
    second, _, observation = capture_bundle(store, source(), b"A", started - timedelta(seconds=30))

    assert first.capture_sequence == 1
    assert second.capture_sequence == 2
    assert observation.state == "possibly_active"


def test_bundle_lineages_settle_independently_with_shared_native_session(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    first_source = source()
    second_source = first_source.model_copy(
        update={"source_id": "source-2", "source_path": "/sessions/source-2.jsonl"}
    )
    capture_bundle(store, first_source, b"A", started)
    capture_bundle(store, second_source, b"B", started + timedelta(seconds=1))
    _, _, observation = capture_bundle(
        store,
        first_source,
        b"A",
        started + timedelta(seconds=31),
    )

    assert observation.state == "settled_unknown"


def test_bundle_member_status_controls_capture_and_lifecycle(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    primary = store.capture_source(source(), b"primary", captured_at=started)
    bundle = store.create_single_source_bundle(source(), primary, "native-session-1")
    bundle = store.add_bundle_members(
        bundle,
        (
            BundleMemberCapture(
                source_id="missing-sidecar",
                source_path="/sessions/missing.txt",
                member_role="tool_result",
                member_capture_status="missing",
                capture_order=1,
                capture_started_at=started,
                capture_completed_at=started,
            ),
        ),
    )

    observation = store.record_lifecycle(bundle.snapshot_bundle_id, terminal_observed=False)
    members = store.load_bundle_members(bundle.snapshot_bundle_id)

    assert bundle.capture_status == "incomplete"
    assert observation.state == "snapshot_incomplete"
    assert members[1].member_capture_status == "missing"
    assert members[1].source_bytes is None

    skewed_store = DuckDBStore(tmp_path / "skewed.duckdb")
    skewed_primary = skewed_store.capture_source(source(), b"changing", captured_at=started)
    skewed_bundle = skewed_store.create_single_source_bundle(
        source(),
        skewed_primary,
        "native-session-1",
        capture_status="skewed",
        primary_capture_status="changed_during_capture",
    )
    skewed_observation = skewed_store.record_lifecycle(
        skewed_bundle.snapshot_bundle_id,
        terminal_observed=False,
    )
    skewed_members = skewed_store.load_bundle_members(skewed_bundle.snapshot_bundle_id)
    assert skewed_members[0].member_capture_status == "changed_during_capture"
    assert skewed_observation.state == "snapshot_incomplete"


def test_missing_member_identity_changes_bundle_content(tmp_path) -> None:
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    content_ids: list[str] = []
    for index, missing_path in enumerate(("/missing/a", "/missing/b")):
        store = DuckDBStore(tmp_path / f"missing-{index}.duckdb")
        captured = store.capture_source(source(), b"same", captured_at=started)
        bundle = store.create_single_source_bundle(source(), captured, "native-session-1")
        bundle = store.add_bundle_members(
            bundle,
            (
                BundleMemberCapture(
                    source_id="missing-source",
                    source_path=missing_path,
                    member_role="tool_result",
                    member_capture_status="missing",
                    capture_order=1,
                    capture_started_at=started,
                    capture_completed_at=started,
                ),
            ),
        )
        content_ids.append(bundle.bundle_content_id)

    assert content_ids[0] != content_ids[1]


def test_topology_selection_detects_changes_to_any_input(tmp_path) -> None:
    selected_parent = tmp_path / "parent.jsonl"
    competing_parent = tmp_path / "competing.jsonl"
    selected_parent.write_bytes(b"selected")
    competing_parent.write_bytes(b"competing")
    member_source = source().model_copy(
        update={
            "metadata": {
                "capture_topology_input_sha256": {
                    str(selected_parent): hashlib.sha256(b"selected").hexdigest(),
                    str(competing_parent): hashlib.sha256(b"competing").hexdigest(),
                },
                "capture_topology_directory": str(tmp_path),
                "capture_topology_directory_members": sorted(
                    [str(selected_parent), str(competing_parent)]
                ),
            }
        }
    )
    members = ((member_source, "related_transcript"),)

    assert topology_inputs_changed(members) is False
    competing_parent.write_bytes(b"changed")
    assert topology_inputs_changed(members) is True
    competing_parent.write_bytes(b"competing")
    (tmp_path / "new-neighbor.jsonl").write_bytes(b"new")
    assert topology_inputs_changed(members) is True


def test_topology_change_skews_bundle_without_existing_members() -> None:
    assert capture_status(False, (), topology_changed=True) == "skewed"


def test_incomplete_capture_interrupts_settling_across_identity_fallback(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    capture_bundle(store, source(), b"same", started)
    capture_bundle(
        store,
        source(),
        b"broken",
        started + timedelta(seconds=31),
        native_identity="parse-failed:source-1",
        capture_status="parse_failed",
    )
    _, _, observation = capture_bundle(
        store,
        source(),
        b"same",
        started + timedelta(seconds=62),
    )

    assert observation.state == "possibly_active"


def test_unbundled_capture_interrupts_settling(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    capture_bundle(store, source(), b"same", started)
    store.capture_source(
        source(),
        b"unbundled",
        captured_at=started + timedelta(seconds=31),
    )
    _, _, observation = capture_bundle(
        store,
        source(),
        b"same",
        started + timedelta(seconds=62),
    )

    assert observation.state == "possibly_active"
    assert observation.evidence["lineage_is_source_consecutive"] is False


def test_snapshot_history_marks_only_latest_and_supports_explicit_selection(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    first, _, _ = capture_bundle(store, source(), b"A", started)
    second, _, _ = capture_bundle(store, source(), b"B", started + timedelta(seconds=31))

    rows = store.list_snapshots()

    assert {row.snapshot_id for row in rows} == {first.snapshot_id, second.snapshot_id}
    assert [row.snapshot_id for row in rows if row.is_latest] == [second.snapshot_id]
    historical = store.snapshot_summary(first.snapshot_id)
    latest = store.latest_snapshot(source().source_id)
    assert historical is not None
    assert latest is not None
    assert historical.snapshot_id == first.snapshot_id
    assert latest.snapshot_id == second.snapshot_id


def test_status_aware_latest_selects_latest_matching_lifecycle(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    first, _, _ = capture_bundle(
        store,
        source(),
        b"terminal",
        started,
        terminal_observed=True,
    )
    capture_bundle(store, source(), b"active", started + timedelta(seconds=31))

    terminal = store.latest_snapshot(
        source().source_id,
        lifecycle_state="terminal_observed",
    )

    assert terminal is not None
    assert terminal.snapshot_id == first.snapshot_id


def test_prune_blocks_current_projection_and_force_deletes_dependencies(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source_row = source()
    captured, bundle, _ = capture_bundle(
        store,
        source_row,
        b"A",
        datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )
    parsed = ParsedSessionBundle(
        session=Session(
            session_id="session-1",
            source_id=source_row.source_id,
            agent_name=source_row.agent_name,
            native_session_id="native-session-1",
        )
    )
    store.insert_parsed_bundle(source_row, parsed, captured, bundle)

    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(captured.snapshot_id)

    result = store.prune_snapshot(captured.snapshot_id, force=True)

    assert result.dependent_source_ids == (source_row.source_id,)
    assert result.dependent_session_ids == ("session-1",)
    assert result.dependent_analysis_run_ids == ()
    assert result.derived_row_counts["analysis_runs"] == 0
    assert result.checkpoint_completed is True
    assert store.snapshot_summary(captured.snapshot_id) is None
    assert store.table_count("sessions") == 0


def test_prune_rolls_back_all_relational_changes_on_failure(tmp_path, monkeypatch) -> None:
    from session_doctor.store import snapshot_history

    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source_row = source()
    captured, bundle, _ = capture_bundle(
        store,
        source_row,
        b"A",
        datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )
    store.insert_parsed_bundle(
        source_row,
        ParsedSessionBundle(
            session=Session(
                session_id="session-1",
                source_id=source_row.source_id,
                agent_name=source_row.agent_name,
                native_session_id="native-session-1",
            )
        ),
        captured,
        bundle,
    )
    before = {
        table: store.table_count(table)
        for table in (
            "source_snapshots",
            "snapshot_bundles",
            "lifecycle_observations",
            "session_sources",
            "sessions",
        )
    }
    original_delete = snapshot_history.delete_source_records

    def fail_after_delete(connection, source_id: str) -> None:
        original_delete(connection, source_id)
        raise RuntimeError("synthetic prune failure")

    monkeypatch.setattr(snapshot_history, "delete_source_records", fail_after_delete)

    with pytest.raises(RuntimeError, match="synthetic prune failure"):
        store.prune_snapshot(captured.snapshot_id, force=True)

    assert {table: store.table_count(table) for table in before} == before


def test_prune_downgrades_settlement_that_depended_on_deleted_capture(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    started = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    first, _, _ = capture_bundle(store, source(), b"same", started)
    _, second_bundle, second_observation = capture_bundle(
        store,
        source(),
        b"same",
        started + timedelta(seconds=31),
    )
    assert second_observation.state == "settled_unknown"
    dependencies = store.snapshot_dependencies(first.snapshot_id)
    assert dependencies.downstream_lifecycle_bundle_ids == (second_bundle.snapshot_bundle_id,)

    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(first.snapshot_id)
    store.prune_snapshot(first.snapshot_id, force=True)

    rewritten = store.lifecycle_for_bundle(second_bundle.snapshot_bundle_id)
    assert rewritten is not None
    assert rewritten.state == "possibly_active"
    assert rewritten.evidence["reason"] == "predecessor_pruned"


def test_schema_v5_history_is_backfilled_without_losing_raw_bytes(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    captured_at = datetime(2026, 7, 13, 12, 0)
    source_bytes = b"retained"
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_migrations (version) VALUES (5)")
        connection.execute(
            """
            CREATE TABLE source_blobs (
                blob_id VARCHAR PRIMARY KEY, content_hash VARCHAR UNIQUE,
                codec VARCHAR, compressed_bytes BLOB, original_byte_length BIGINT,
                created_at TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE TABLE logical_sources (logical_source_id VARCHAR PRIMARY KEY, "
            "agent_name VARCHAR, source_kind VARCHAR, source_path VARCHAR, "
            "first_seen_at TIMESTAMP, metadata_json VARCHAR)"
        )
        connection.execute(
            """
            CREATE TABLE source_snapshots (
                snapshot_id VARCHAR PRIMARY KEY, source_id VARCHAR, agent_name VARCHAR,
                source_kind VARCHAR, source_path VARCHAR, discovered_at VARCHAR,
                native_session_id VARCHAR, parent_source_id VARCHAR,
                source_metadata_json VARCHAR, logical_source_id VARCHAR, blob_id VARCHAR,
                snapshot_content_id VARCHAR, capture_sequence BIGINT, captured_at TIMESTAMP,
                native_modified_at TIMESTAMP, capture_status VARCHAR,
                previous_snapshot_id VARCHAR
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_bundles (
                snapshot_bundle_id VARCHAR PRIMARY KEY, bundle_content_id VARCHAR,
                agent_name VARCHAR, native_session_identity VARCHAR,
                primary_snapshot_id VARCHAR, native_identity_status VARCHAR,
                native_bundle_capture_sequence BIGINT, previous_snapshot_bundle_id VARCHAR,
                captured_at TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_bundle_members (
                snapshot_bundle_id VARCHAR, logical_source_id VARCHAR, snapshot_id VARCHAR,
                capture_order INTEGER, member_role VARCHAR, member_capture_status VARCHAR
            )
            """
        )
        connection.execute(
            "INSERT INTO source_blobs VALUES (?, ?, 'zlib', ?, ?, ?)",
            [
                "blob-1",
                hashlib.sha256(source_bytes).hexdigest(),
                zlib.compress(source_bytes, level=6),
                len(source_bytes),
                captured_at,
            ],
        )
        connection.execute(
            "INSERT INTO logical_sources VALUES "
            "('logical-1', 'pi', 'root_session', '/sessions/source-1.jsonl', ?, '{}')",
            [captured_at],
        )
        connection.execute(
            """
            INSERT INTO source_snapshots VALUES (
                'snapshot-1', 'source-1', 'pi', 'root_session',
                '/sessions/source-1.jsonl', NULL, NULL, NULL, '{}', 'logical-1',
                'blob-1', 'content-1', 1, ?, NULL, 'captured', NULL
            )
            """,
            [captured_at],
        )
        connection.execute(
            "INSERT INTO snapshot_bundles VALUES "
            "('bundle-1', 'bundle-content-1', 'pi', 'native-1', 'snapshot-1', "
            "'observed', 1, NULL, ?)",
            [captured_at],
        )
        connection.execute(
            "INSERT INTO snapshot_bundle_members VALUES "
            "('bundle-1', 'logical-1', 'snapshot-1', 0, 'primary', 'captured')"
        )
        connection.execute(
            """
            INSERT INTO source_snapshots VALUES (
                'snapshot-2', 'source-1', 'pi', 'root_session',
                '/sessions/source-1.jsonl', NULL, NULL, NULL, '{}', 'logical-1',
                'blob-1', 'content-1', 2, ?, NULL, 'captured', 'snapshot-1'
            )
            """,
            [captured_at - timedelta(hours=1)],
        )
        connection.execute(
            "INSERT INTO snapshot_bundles VALUES "
            "('bundle-2', 'bundle-content-1', 'pi', 'parse-failed:source-1', 'snapshot-2', "
            "'fallback_parse_failed', 2, 'bundle-1', ?)",
            [captured_at - timedelta(hours=1)],
        )
        connection.execute(
            "INSERT INTO snapshot_bundle_members VALUES "
            "('bundle-2', 'logical-1', 'snapshot-2', 0, 'primary', 'captured')"
        )
        connection.execute(
            """
            INSERT INTO source_snapshots VALUES (
                'snapshot-3', 'source-1', 'pi', 'root_session',
                '/sessions/source-1.jsonl', NULL, NULL, NULL, '{}', 'logical-1',
                'blob-1', 'content-1', 3, ?, NULL, 'captured', 'snapshot-2'
            )
            """,
            [captured_at + timedelta(hours=1)],
        )
        connection.execute(
            "INSERT INTO snapshot_bundles VALUES "
            "('bundle-3', 'bundle-content-1', 'pi', 'native-1', 'snapshot-3', "
            "'observed', 3, 'bundle-2', ?)",
            [captured_at + timedelta(hours=1)],
        )
        connection.execute(
            "INSERT INTO snapshot_bundle_members VALUES "
            "('bundle-3', 'logical-1', 'snapshot-3', 0, 'primary', 'captured')"
        )
        connection.execute(
            """
            INSERT INTO source_snapshots VALUES (
                'snapshot-4', 'source-1', 'pi', 'root_session',
                '/sessions/source-1.jsonl', NULL, NULL, NULL, '{}', 'logical-1',
                'blob-1', 'content-1', 4, ?, NULL, 'captured', 'snapshot-3'
            )
            """,
            [captured_at + timedelta(hours=2)],
        )
        connection.execute(
            "INSERT INTO snapshot_bundles VALUES "
            "('bundle-4', 'bundle-content-1', 'pi', 'parse-failed:source-1', 'snapshot-4', "
            "'fallback_parse_failed', 4, 'bundle-3', ?)",
            [captured_at + timedelta(hours=2)],
        )
        connection.execute(
            "INSERT INTO snapshot_bundle_members VALUES "
            "('bundle-4', 'logical-1', 'snapshot-4', 0, 'primary', 'captured')"
        )

    store = DuckDBStore(database_path)
    store.initialize()

    assert store.load_snapshot_bytes("snapshot-1") == source_bytes
    summary = store.snapshot_summary("snapshot-1")
    assert summary is not None
    assert summary.lifecycle_state == "possibly_active"
    with duckdb.connect(str(database_path), read_only=True) as connection:
        lineage = connection.execute(
            """
            SELECT snapshot_bundle_id, lineage_capture_sequence,
                previous_lineage_bundle_id
            FROM bundle_capture_metadata
            WHERE lineage_id = (
                SELECT lineage_id FROM bundle_capture_metadata
                WHERE snapshot_bundle_id = 'bundle-1'
            )
            ORDER BY lineage_capture_sequence
            """
        ).fetchall()
        states = connection.execute(
            "SELECT state FROM lifecycle_observations ORDER BY snapshot_bundle_id"
        ).fetchall()
        capture_statuses = connection.execute(
            "SELECT capture_status FROM bundle_capture_metadata ORDER BY snapshot_bundle_id"
        ).fetchall()
    assert lineage == [("bundle-1", 1, None), ("bundle-3", 2, "bundle-1")]
    assert states == [
        ("possibly_active",),
        ("snapshot_incomplete",),
        ("possibly_active",),
        ("snapshot_incomplete",),
    ]
    assert capture_statuses == [
        ("complete",),
        ("parse_failed",),
        ("complete",),
        ("parse_failed",),
    ]

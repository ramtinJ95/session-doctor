from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from session_doctor.adapters import ParsedSessionBundle
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
):
    captured = store.capture_source(source_row, source_bytes, captured_at=captured_at)
    bundle = store.create_single_source_bundle(
        source_row,
        captured,
        "native-session-1",
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
    assert store.snapshot_summary(captured.snapshot_id) is None
    assert store.table_count("sessions") == 0


def test_schema_v5_history_is_backfilled_without_losing_raw_bytes(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    captured, _bundle, _observation = capture_bundle(
        store,
        source(),
        b"retained",
        datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("DROP TABLE lifecycle_observations")
        connection.execute("DROP TABLE bundle_member_capture_metadata")
        connection.execute("DROP TABLE bundle_capture_metadata")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
        connection.execute("INSERT INTO schema_migrations (version) VALUES (5)")

    store.initialize()

    assert store.load_snapshot_bytes(captured.snapshot_id) == b"retained"
    summary = store.snapshot_summary(captured.snapshot_id)
    assert summary is not None
    assert summary.lifecycle_state == "possibly_active"

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from session_doctor.adapters.codex import CodexAdapter
from session_doctor.cli import app
from session_doctor.normalization_workflow import normalize_snapshot
from session_doctor.schemas import AgentName, SessionSource
from session_doctor.store import DuckDBStore

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "codex" / "basic-session.jsonl"
runner = CliRunner()


class LegacyCodexAdapter(CodexAdapter):
    version = "0.0.9"


def test_bundle_without_run_reports_missing_coverage(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/sessions/source-1.jsonl",
    )
    captured = store.capture_source(
        source,
        b"{}\n",
        captured_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )
    bundle = store.create_single_source_bundle(source, captured, "native-1")

    coverage = store.normalization_coverage(
        bundle.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version=CodexAdapter.version,
    )

    assert coverage.status == "missing"
    assert coverage.current_normalization_run_id is None
    assert coverage.available_normalization_run_ids == ()


def ingest_fixture(database_path: Path) -> tuple[DuckDBStore, str]:
    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(FIXTURE_PATH),
            "--db",
            str(database_path),
        ],
    )
    assert result.exit_code == 0
    store = DuckDBStore(database_path)
    snapshots = store.list_snapshots()
    assert snapshots
    return store, next(row.snapshot_id for row in snapshots if row.is_latest)


def test_parser_versions_coexist_and_replay_is_additive(tmp_path) -> None:
    store, snapshot_id = ingest_fixture(tmp_path / "session-doctor.duckdb")
    summary = store.snapshot_summary(snapshot_id)
    assert summary is not None
    assert summary.snapshot_bundle_id is not None
    current = store.normalization_coverage(
        summary.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version=CodexAdapter.version,
    )
    assert current.status == "current"
    assert current.current_normalization_run_id is not None

    legacy_run = normalize_snapshot(LegacyCodexAdapter(), store, snapshot_id)
    run_count = store.table_count("normalization_runs")
    entity_count = store.table_count("normalized_entities")
    replayed_legacy = normalize_snapshot(LegacyCodexAdapter(), store, snapshot_id)

    assert legacy_run.normalization_run_id != current.current_normalization_run_id
    assert replayed_legacy.normalization_run_id == legacy_run.normalization_run_id
    assert store.table_count("normalization_runs") == run_count == 2
    assert store.table_count("normalized_entities") == entity_count
    stored_current = store.load_normalization(current.current_normalization_run_id)
    stored_legacy = store.load_normalization(legacy_run.normalization_run_id)
    assert stored_current is not None
    assert stored_legacy is not None
    assert stored_current.bundle == stored_legacy.bundle


def test_coverage_selection_is_deterministic_and_read_only(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store, snapshot_id = ingest_fixture(database_path)
    summary = store.snapshot_summary(snapshot_id)
    assert summary is not None
    assert summary.snapshot_bundle_id is not None
    before = {
        table: store.table_count(table)
        for table in (
            "normalization_runs",
            "normalization_run_bundles",
            "normalized_entities",
        )
    }

    first = store.normalization_coverage(
        summary.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version=CodexAdapter.version,
    )
    second = store.normalization_coverage(
        summary.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version=CodexAdapter.version,
    )
    stale = store.normalization_coverage(
        summary.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version="future-parser",
    )

    assert first == second
    assert first.status == "current"
    assert stale.status == "stale"
    assert stale.current_normalization_run_id is None
    assert {table: store.table_count(table) for table in before} == before

    status_result = runner.invoke(
        app,
        [
            "normalizations",
            "status",
            snapshot_id,
            "--db",
            str(database_path),
        ],
    )
    assert status_result.exit_code == 0
    assert json.loads(status_result.stdout)["status"] == "current"
    assert {table: store.table_count(table) for table in before} == before


def test_explicit_cli_replay_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store, snapshot_id = ingest_fixture(database_path)
    before_entities = store.table_count("normalized_entities")

    first = runner.invoke(
        app,
        ["normalizations", "replay", snapshot_id, "--db", str(database_path)],
    )
    second = runner.invoke(
        app,
        ["normalizations", "replay", snapshot_id, "--db", str(database_path)],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert store.table_count("normalization_runs") == 1
    assert store.table_count("normalized_entities") == before_entities


def test_historical_replay_uses_only_stored_bytes(tmp_path) -> None:
    source_path = tmp_path / "historical.jsonl"
    source_path.write_bytes(FIXTURE_PATH.read_bytes())
    database_path = tmp_path / "session-doctor.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_path),
            "--db",
            str(database_path),
        ],
    )
    assert result.exit_code == 0
    store = DuckDBStore(database_path)
    snapshot_id = store.list_snapshots()[0].snapshot_id
    source_path.unlink()

    replayed = normalize_snapshot(LegacyCodexAdapter(), store, snapshot_id)

    assert store.load_normalization(replayed.normalization_run_id) is not None


def test_prune_reports_normalization_and_preserves_shared_run(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store, _ = ingest_fixture(database_path)
    _, _ = ingest_fixture(database_path)
    snapshots = sorted(store.list_snapshots(), key=lambda row: row.capture_sequence)
    assert len(snapshots) == 2
    assert store.table_count("normalization_runs") == 1
    assert store.table_count("normalization_run_bundles") == 2
    dependencies = store.snapshot_dependencies(snapshots[0].snapshot_id)
    assert len(dependencies.normalization_run_ids) == 1

    result = store.prune_snapshot(snapshots[0].snapshot_id, force=True)

    assert result.dependent_normalization_run_ids == dependencies.normalization_run_ids
    assert store.table_count("normalization_runs") == 1
    assert store.table_count("normalization_run_bundles") == 1

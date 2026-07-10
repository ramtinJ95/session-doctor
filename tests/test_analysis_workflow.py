from __future__ import annotations

from pathlib import Path

import pytest

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis_workflow import (
    AnalysisArtifactError,
    AnalysisPersistenceError,
    SessionNotLoadableError,
    analyze_session,
)
from session_doctor.schemas import AgentName, Session, SessionSource
from session_doctor.store import DuckDBStore


def test_analysis_workflow_reports_missing_session_as_typed_failure(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    store.initialize()

    with pytest.raises(SessionNotLoadableError) as failure:
        analyze_session(
            store,
            "missing-session",
            database_path,
            artifact=None,
            no_artifact=True,
        )

    assert failure.value.code.value == "session_not_loadable"
    assert failure.value.safe_message == "Session could not be loaded"
    assert failure.value.not_found is True


def test_analysis_workflow_maps_artifact_failure_without_persisting(tmp_path) -> None:
    database_path, store = store_with_empty_session(tmp_path)

    with pytest.raises(AnalysisArtifactError) as failure:
        analyze_session(
            store,
            "session-a",
            database_path,
            artifact=tmp_path,
            no_artifact=False,
        )

    assert failure.value.code.value == "artifact_write_failed"
    assert failure.value.path == tmp_path
    assert store.table_count("analysis_runs") == 0


def test_analysis_workflow_maps_persistence_failure_and_preserves_cause(
    tmp_path,
    monkeypatch,
) -> None:
    database_path, store = store_with_empty_session(tmp_path)

    def fail_persistence(*args, **kwargs) -> None:
        raise RuntimeError("private persistence detail")

    monkeypatch.setattr(store, "replace_analysis_rows", fail_persistence)

    with pytest.raises(AnalysisPersistenceError) as failure:
        analyze_session(
            store,
            "session-a",
            database_path,
            artifact=None,
            no_artifact=False,
        )

    assert failure.value.code.value == "persistence_failed"
    assert failure.value.safe_message == "Analysis results could not be persisted"
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert not (tmp_path / "artifacts" / "session-a-analysis.json").exists()
    assert list((tmp_path / "artifacts").glob("*.tmp")) == []


def test_analysis_workflow_publishes_artifact_after_persistence(tmp_path) -> None:
    database_path, store = store_with_empty_session(tmp_path)

    result = analyze_session(
        store,
        "session-a",
        database_path,
        artifact=None,
        no_artifact=False,
    )

    artifact_path = tmp_path / "artifacts" / "session-a-analysis.json"
    assert artifact_path.exists()
    assert result.analysis_run.artifact_path == str(artifact_path)
    assert list(artifact_path.parent.glob("*.tmp")) == []


def store_with_empty_session(tmp_path: Path) -> tuple[Path, DuckDBStore]:
    database_path = tmp_path / "session-doctor.duckdb"
    source = SessionSource(
        source_id="source-a",
        agent_name=AgentName.CODEX,
        source_path="/tmp/session-a.jsonl",
    )
    store = DuckDBStore(database_path)
    store.insert_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=Session(
                session_id="session-a",
                source_id=source.source_id,
                agent_name=source.agent_name,
            )
        ),
    )
    return database_path, store

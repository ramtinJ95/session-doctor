from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime

from analysis.fixtures import analysis_fixture_bundle
from typer.testing import CliRunner

from session_doctor.analysis_workflow import analyze_session
from session_doctor.cli import app
from session_doctor.diagnostic_models import (
    DiagnosticFailedCommandPattern,
    DiagnosticRecurrenceContext,
    RecurrenceAnalysisExclusions,
    RecurrenceEvidence,
    RecurrenceFamilyExclusions,
    RecurrenceTemporalExclusions,
)
from session_doctor.ids import stable_id
from session_doctor.report_models import FailureGroupEvidence, FileLoopEvidence
from session_doctor.report_payload import build_session_report
from session_doctor.report_renderers import render_session_report_markdown
from session_doctor.schemas import AgentName, AnalysisRun, SessionFeature, SessionSource
from session_doctor.store import TABLE_NAMES, DuckDBStore

runner = CliRunner()


def test_report_payload_is_stable_explainable_and_private_by_default(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    report = build_session_report(snapshot)
    payload = report.model_dump(mode="json")
    serialized = report.model_dump_json()

    assert list(payload) == [
        "schema_version",
        "session",
        "privacy",
        "analysis",
        "summary",
        "scores",
        "classifications",
        "sequence",
        "evidence",
        "ending",
        "project_context",
        "observations",
        "review_actions",
        "limitations",
    ]
    assert report.schema_version == 2
    assert report.analysis.status == "current"
    assert [row.name for row in report.scores] == [
        "friction_score",
        "stuckness_score",
        "prompt_clarity_risk",
        "agent_fit_risk",
        "project_complexity_signal",
    ]
    assert all(row.component_values for row in report.scores)
    assert report.sequence.ordering_basis == "source_record_order"
    assert report.sequence.first_record_index == 1
    assert report.sequence.last_record_index == 10
    assert report.sequence.total_resolved_activities == 10
    assert report.sequence.total_unresolved_activities == 0
    assert len(report.sequence.bins) == 10
    assert (
        sum(sum(bin_row.counts.model_dump().values()) for bin_row in report.sequence.bins)
        == report.sequence.total_resolved_activities
    )
    assert report.sequence.resolved_activity_counts.model_dump() == {
        "user_message": 4,
        "assistant_message": 1,
        "tool_call": 0,
        "tool_result": 0,
        "tool_failure": 1,
        "command_success": 0,
        "command_failure": 2,
        "command_unknown": 0,
        "file_activity": 2,
        "parse_warning": 0,
    }
    assert report.sequence.evidence_markers == sorted(
        report.sequence.evidence_markers,
        key=lambda row: (row.record_index, row.category, row.evidence_id, row.source_event_id),
    )
    assert report.privacy.message_text_included is False
    assert "Please fix the failing pytest" not in serialized
    assert "I will run the tests" not in serialized
    assert "hash-failure" not in serialized
    assert "tool-error-hash" not in serialized
    assert "matched_markers" not in serialized
    assert "metadata" not in serialized


def test_report_show_text_discloses_only_displayed_evidence_messages(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    report = build_session_report(snapshot, limit=1, show_text=True)
    serialized = report.model_dump_json()

    assert report.privacy.message_text_included is True
    assert "Please fix the pytest failure" in serialized
    assert "I will run the tests" not in serialized
    assert all(section.displayed <= 1 for section in report.evidence.values())
    assert all(
        section.omitted == section.total - section.displayed for section in report.evidence.values()
    )
    complete_markers = build_session_report(snapshot, limit=10).sequence.evidence_markers
    assert report.sequence.evidence_markers == complete_markers


def test_report_sequence_preserves_unresolved_group_and_file_markers(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None
    replaced_features = []
    for feature in snapshot.analysis.session_features:
        evidence = deepcopy(feature.evidence)
        if feature.feature_name == "repeated_failure_count":
            groups = evidence.get("groups")
            assert isinstance(groups, list)
            for group in groups:
                assert isinstance(group, dict)
                group["source_event_ids"] = [
                    *group.get("source_event_ids", []),
                    "missing-failure-event",
                ]
        elif feature.feature_name == "same_file_edited_repeatedly_count":
            evidence = {
                "paths": ["/tmp/example.py"],
                "source_event_ids_by_path": {
                    "/tmp/example.py": ["event-4", "event-7", "missing-file-event"]
                },
            }
        replaced_features.append(feature.model_copy(update={"evidence": evidence}))
    snapshot = replace(
        snapshot,
        analysis=replace(snapshot.analysis, session_features=tuple(replaced_features)),
    )

    report = build_session_report(snapshot)

    failure_item = report.evidence["repeated_failures"].items[0]
    file_item = report.evidence["repeated_file_edits"].items[0]
    assert isinstance(failure_item, FailureGroupEvidence)
    assert isinstance(file_item, FileLoopEvidence)
    assert failure_item.unresolved_source_event_ids == ["missing-failure-event"]
    assert file_item.unresolved_source_event_ids == ["missing-file-event"]
    unresolved = {row.category: row.count for row in report.sequence.unresolved_evidence_markers}
    assert unresolved["repeated_failures"] >= 1
    assert unresolved["repeated_file_edits"] >= 1


def test_report_stale_analysis_is_successful_explicit_partial_output(tmp_path) -> None:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "stale.duckdb")
    store.insert_parsed_bundle(source_for_bundle(), bundle)
    stale_run = AnalysisRun(
        analysis_run_id="stale-run",
        session_id=bundle.session.session_id,
        analyzer_version="phase5",
    )
    stale_feature = SessionFeature(
        session_feature_id="stale-score",
        analysis_run_id=stale_run.analysis_run_id,
        session_id=bundle.session.session_id,
        feature_name="friction_score",
        feature_value="1",
        score=1,
    )
    store.replace_analysis_rows(stale_run, [], [stale_feature], [])
    snapshot = store.load_diagnostic_snapshot(bundle.session.session_id)
    assert snapshot is not None

    report = build_session_report(snapshot)

    assert report.analysis.status == "stale"
    assert report.analysis.observed_analyzer_version == "phase5"
    assert report.analysis.action == "session-doctor analyze session-1"
    assert report.scores == []
    assert report.classifications == []
    assert all(section.status == "unavailable" for section in report.evidence.values())
    assert report.ending.status == "unavailable"
    assert any(row.code == "analysis_stale" for row in report.limitations)


def test_report_missing_analysis_is_successful_explicit_partial_output(tmp_path) -> None:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "missing-analysis.duckdb")
    store.insert_parsed_bundle(source_for_bundle(), bundle)
    snapshot = store.load_diagnostic_snapshot(bundle.session.session_id)
    assert snapshot is not None

    report = build_session_report(snapshot, show_text=True)

    assert report.analysis.status == "missing"
    assert report.analysis.observed_analyzer_version is None
    assert report.scores == []
    assert report.privacy.message_text_included is True
    assert all(section.items == [] for section in report.evidence.values())
    assert "Please fix" not in report.model_dump_json()


def test_report_markdown_has_stable_sections_and_one_trailing_newline(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    markdown = render_session_report_markdown(build_session_report(snapshot))

    assert markdown.startswith("# Session report: `session-1`\n")
    assert "## Evidence" in markdown
    assert "## Historical project recurrence" in markdown
    assert "\x1b[" not in markdown
    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


def test_report_bounds_historical_recurrence_and_preserves_totals(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None
    evidence = RecurrenceEvidence(
        event_count=3,
        selected_session_event_count=1,
        session_count=2,
        root_family_count=2,
        top_level_session_count=2,
        sidechain_session_count=0,
        agents=("codex",),
        first_at=datetime(2026, 1, 1),
        most_recent_at=datetime(2026, 1, 2),
    )
    recurrence = DiagnosticRecurrenceContext(
        status="available",
        reason=None,
        scope_path="~/project",
        scope_source="session_project_path",
        window_start=datetime(2025, 10, 13),
        evidence_cutoff=datetime(2026, 1, 2),
        family_exclusions=RecurrenceFamilyExclusions(),
        temporal_exclusions=RecurrenceTemporalExclusions(),
        problematic_file_analysis_exclusions=RecurrenceAnalysisExclusions(),
        problematic_files_status="available",
        problematic_files_reason=None,
        failed_commands=(
            DiagnosticFailedCommandPattern("pattern-a", "pytest -q", evidence),
            DiagnosticFailedCommandPattern("pattern-b", "ruff check", evidence),
        ),
        failed_tool_results=(),
        problematic_files=(),
    )

    report = build_session_report(replace(snapshot, recurrence=recurrence), limit=1)

    section = report.project_context.failed_commands
    assert (section.total, section.displayed, section.omitted) == (2, 1, 1)
    assert section.items[0].pattern_id == "pattern-a"
    assert any(row.code == "project_recurrence_observed" for row in report.observations)


def test_report_does_not_disclose_or_claim_unresolved_file_loop_paths(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None
    file_feature = next(
        row
        for row in snapshot.analysis.session_features
        if row.feature_name == "same_file_edited_repeatedly_count"
    )
    private_path = "/outside-home/PRIVATE_UNRESOLVED_FILE.py"
    replaced_features = tuple(
        row.model_copy(
            update={
                "evidence": {
                    "paths": [private_path],
                    "source_event_ids_by_path": {private_path: ["event-1", "event-2"]},
                }
            }
        )
        if row.session_feature_id == file_feature.session_feature_id
        else row
        for row in snapshot.analysis.session_features
    )
    snapshot = replace(
        snapshot,
        analysis=replace(snapshot.analysis, session_features=replaced_features),
    )

    report = build_session_report(snapshot)
    serialized = report.model_dump_json()

    assert report.evidence["repeated_file_edits"].items == []
    assert "PRIVATE_UNRESOLVED_FILE" not in serialized
    limitation = next(
        row for row in report.limitations if row.code == "unresolved_analysis_references"
    )
    assert stable_id("unresolved-file-loop", private_path) in limitation.evidence_ids


def test_report_cli_supports_all_formats_without_mutating_store(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    before = {table: store.table_count(table) for table in TABLE_NAMES}

    json_result = runner.invoke(
        app, ["report", session_id, "--db", str(store.database_path), "--format", "json"]
    )
    markdown_result = runner.invoke(
        app,
        ["report", session_id, "--db", str(store.database_path), "--format", "markdown"],
    )
    terminal_result = runner.invoke(app, ["report", session_id, "--db", str(store.database_path)])

    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout)["schema_version"] == 2
    assert markdown_result.exit_code == 0
    assert markdown_result.stdout.startswith("# Session report")
    assert terminal_result.exit_code == 0
    assert "Session report: session-1" in terminal_result.stdout
    assert {table: store.table_count(table) for table in TABLE_NAMES} == before
    assert not (tmp_path / "artifacts").exists()


def test_report_cli_rejects_invalid_options_and_missing_session(tmp_path, monkeypatch) -> None:
    store, _ = analyzed_store(tmp_path)

    invalid_format = runner.invoke(
        app,
        ["report", "session-1", "--db", str(store.database_path), "--format", "html"],
    )
    invalid_limit = runner.invoke(
        app,
        ["report", "session-1", "--db", str(store.database_path), "--limit", "0"],
    )
    missing = runner.invoke(app, ["report", "missing", "--db", str(store.database_path)])
    matching_agent = runner.invoke(
        app,
        ["report", "session-1", "--agent", "codex", "--db", str(store.database_path)],
    )

    def fail_snapshot_load(*args, **kwargs):
        raise AssertionError("mismatched diagnostic snapshot must not be loaded")

    monkeypatch.setattr(DuckDBStore, "load_diagnostic_snapshot", fail_snapshot_load)
    mismatched_agent = runner.invoke(
        app,
        ["report", "session-1", "--agent", "pi", "--db", str(store.database_path)],
    )

    assert invalid_format.exit_code == 2
    assert "Invalid --format" in invalid_format.stdout
    assert invalid_limit.exit_code == 2
    assert "Invalid --limit" in invalid_limit.stdout
    assert missing.exit_code == 1
    assert "Session not found: missing" in missing.stdout
    assert matching_agent.exit_code == 0
    assert mismatched_agent.exit_code == 1
    assert "belongs to codex, not pi" in mismatched_agent.stdout


def analyzed_store(tmp_path) -> tuple[DuckDBStore, str]:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "report.duckdb")
    store.insert_parsed_bundle(source_for_bundle(), bundle)
    analyze_session(
        store,
        bundle.session.session_id,
        store.database_path,
        artifact=None,
        no_artifact=True,
    )
    return store, bundle.session.session_id


def source_for_bundle() -> SessionSource:
    return SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/private/source.jsonl",
    )

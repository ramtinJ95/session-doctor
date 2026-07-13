from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from analysis.fixtures import analysis_fixture_bundle
from typer.testing import CliRunner

from session_doctor.analysis_workflow import analyze_session
from session_doctor.cli import app
from session_doctor.html import HtmlRenderError, render_report_html
from session_doctor.html.charts import RISK_MARKER_CATEGORIES
from session_doctor.report_payload import build_session_report
from session_doctor.schemas import AgentName, AnalysisRun, SessionFeature, SessionSource
from session_doctor.store import TABLE_NAMES, DuckDBStore

runner = CliRunner()
FIXTURE_ROOT = Path(__file__).parent / "fixtures"


class StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.landmarks: list[str] = []
        self.headings: list[str] = []
        self.svg_labels = 0
        self.details = 0
        self._heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"header", "main", "footer"}:
            self.landmarks.append(tag)
        if tag in {"h1", "h2", "h3"}:
            self._heading = ""
        if tag == "svg" and attributes.get("role") == "img" and attributes.get("aria-labelledby"):
            self.svg_labels += 1
        if tag == "details":
            self.details += 1

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3"} and self._heading is not None:
            self.headings.append(self._heading.strip())
            self._heading = None


def test_report_html_is_deterministic_semantic_offline_and_private(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None
    report = build_session_report(snapshot)

    first = render_report_html(report)
    second = render_report_html(report)
    parser = StructureParser()
    parser.feed(first)

    assert first == second
    assert first.startswith('<!doctype html>\n<html lang="en">')
    assert first.endswith("</body>\n</html>\n")
    assert first.count("</html>") == 1
    assert parser.landmarks == ["header", "main", "footer"]
    assert [heading for heading in parser.headings if heading in EXPECTED_H2] == EXPECTED_H2
    assert parser.svg_labels == 1
    evidence_sections_with_rows = sum(bool(section.items) for section in report.evidence.values())
    assert parser.details >= evidence_sections_with_rows
    assert "Text alternative: session sequence activity totals" in first
    assert 'class="marker marker-neutral"' in first
    assert 'class="marker marker-risk"' in first
    assert "Failure, warning, or negative evidence" in first
    assert "Exact ending evidence references" in first
    assert report.ending.late_failed_command_ids[0] in first
    assert "Content-Security-Policy" in first
    assert "default-src &#x27;none&#x27;" in first
    assert "prefers-color-scheme: dark" in first
    assert "prefers-reduced-motion: reduce" in first
    assert "@media print" in first
    assert "overflow-wrap: anywhere" in first
    assert "https://" not in first
    assert "http://" not in first
    assert "fetch(" not in first
    assert "localStorage" not in first
    assert "Please fix the failing pytest" not in first
    assert "I will run the tests" not in first
    assert str(store.database_path) not in first
    without_script = first[: first.index("<script>")] + first[first.index("</script>") + 9 :]
    assert all(section in without_script for section in EXPECTED_H2)
    assert "<details" in without_script


def test_sequence_marker_semantics_distinguish_negative_and_neutral_evidence() -> None:
    assert RISK_MARKER_CATEGORIES >= {
        "repeated_requests",
        "corrections",
        "frustration_markers",
        "ambiguity_markers",
        "scope_boundaries",
        "stop_or_pause_markers",
        "command_failures",
        "tool_failures",
        "repeated_failures",
        "repeated_file_edits",
    }
    assert RISK_MARKER_CATEGORIES.isdisjoint(
        {"score", "classification", "classification_evidence", "ending"}
    )


def test_report_html_show_text_is_bounded_to_displayed_evidence(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    html = render_report_html(build_session_report(snapshot, limit=1, show_text=True))

    assert "Please fix the pytest failure" in html
    assert "I will run the tests" not in html
    assert "Displayed bounded evidence text included" in html


def test_report_html_escapes_hostile_text_and_attributes(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None
    report = build_session_report(snapshot)
    hostile = '"></style><img src=x onerror="alert(1)">&'
    hostile_report = report.model_copy(
        update={
            "session": report.session.model_copy(
                update={"session_id": hostile, "project_hint": hostile}
            ),
            "scores": [report.scores[0].model_copy(update={"name": hostile})],
            "classifications": [
                report.classifications[0].model_copy(
                    update={"label": hostile, "evidence_summary": hostile}
                )
            ],
            "observations": [report.observations[0].model_copy(update={"summary": hostile})],
        }
    )

    html = render_report_html(hostile_report)

    assert hostile not in html
    assert "&lt;/style&gt;&lt;img" in html
    assert 'aria-label="&quot;&gt;' in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_report_html_keeps_missing_analysis_and_empty_sequence_explicit(tmp_path) -> None:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "missing.duckdb")
    store.insert_untracked_parsed_bundle(
        source_for_bundle(), bundle.model_copy(update={"raw_events": [], "messages": []})
    )
    snapshot = store.load_diagnostic_snapshot(bundle.session.session_id)
    assert snapshot is not None

    html = render_report_html(build_session_report(snapshot))

    assert "Missing analysis" in html
    assert "session-doctor analyze session-1" in html
    assert "Scores are unavailable because analysis is missing" in html
    assert "No resolved source record range is available" in html
    assert "analysis_missing" in html


def test_report_html_keeps_stale_analysis_recovery_explicit(tmp_path) -> None:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "stale.duckdb")
    store.insert_untracked_parsed_bundle(source_for_bundle(), bundle)
    stale_run = AnalysisRun(
        analysis_run_id="stale-run",
        session_id=bundle.session.session_id,
        analyzer_version="phase5",
    )
    stale_score = SessionFeature(
        session_feature_id="stale-score",
        analysis_run_id=stale_run.analysis_run_id,
        session_id=bundle.session.session_id,
        feature_name="friction_score",
        feature_value="1",
        score=1,
    )
    store.replace_analysis_rows(stale_run, [], [stale_score], [])
    snapshot = store.load_diagnostic_snapshot(bundle.session.session_id)
    assert snapshot is not None

    html = render_report_html(build_session_report(snapshot))

    assert "Stale analysis" in html
    assert "session-doctor analyze session-1" in html
    assert "Scores are unavailable because analysis is stale" in html
    assert "analysis_stale" in html
    assert "<progress" not in html


@pytest.mark.parametrize("output_format", ["terminal", "markdown", "json"])
def test_report_cli_rejects_output_for_non_html_formats(tmp_path, output_format) -> None:
    store, session_id = analyzed_store(tmp_path)

    result = runner.invoke(
        app,
        [
            "report",
            session_id,
            "--db",
            str(store.database_path),
            "--format",
            output_format,
            "--output",
            str(tmp_path / "report.html"),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid --output" in result.stdout


def test_report_cli_html_validates_output_before_snapshot_loading(tmp_path, monkeypatch) -> None:
    store, session_id = analyzed_store(tmp_path)

    def fail_snapshot(*args, **kwargs):
        raise AssertionError("snapshot must not load")

    monkeypatch.setattr(DuckDBStore, "load_diagnostic_snapshot", fail_snapshot)
    missing = runner.invoke(
        app,
        ["report", session_id, "--db", str(store.database_path), "--format", "html"],
    )
    suffix = runner.invoke(
        app,
        [
            "report",
            session_id,
            "--db",
            str(store.database_path),
            "--format",
            "html",
            "--output",
            str(tmp_path / "report.txt"),
        ],
    )
    parent = runner.invoke(
        app,
        [
            "report",
            session_id,
            "--db",
            str(store.database_path),
            "--format",
            "html",
            "--output",
            str(tmp_path / "missing" / "report.html"),
        ],
    )
    directory_target = tmp_path / "directory.html"
    directory_target.mkdir()
    directory = runner.invoke(
        app,
        [
            "report",
            session_id,
            "--db",
            str(store.database_path),
            "--format",
            "html",
            "--output",
            str(directory_target),
        ],
    )

    assert missing.exit_code == 2
    assert "Missing --output" in missing.stdout
    assert suffix.exit_code == 2
    assert ".html or .htm" in suffix.stdout
    assert parent.exit_code == 2
    assert "parent directory does not exist" in parent.stdout
    assert directory.exit_code == 2
    assert "destination must be a regular file" in directory.stdout


def test_report_cli_html_atomically_replaces_one_file_and_keeps_database_read_only(
    tmp_path,
) -> None:
    store, session_id = analyzed_store(tmp_path)
    before = {table_name: store.table_count(table_name) for table_name in TABLE_NAMES}
    output = tmp_path / "report.html"
    output.write_bytes(b"old report")

    result = runner.invoke(
        app,
        [
            "report",
            session_id,
            "--db",
            str(store.database_path),
            "--format",
            "html",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == f"Wrote HTML report: {output}"
    assert output.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "<!doctype html>" not in result.stdout
    assert {table_name: store.table_count(table_name) for table_name in TABLE_NAMES} == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["report.duckdb", "report.html"]


def test_report_cli_render_failure_preserves_existing_output(tmp_path, monkeypatch) -> None:
    store, session_id = analyzed_store(tmp_path)
    output = tmp_path / "report.html"
    original = b"existing report"
    output.write_bytes(original)

    def fail_render(*args, **kwargs):
        raise HtmlRenderError("synthetic failure")

    monkeypatch.setattr("session_doctor.cli.render_report_html", fail_render)
    result = invoke_html(store, session_id, output)

    assert result.exit_code == 1
    assert "Could not write HTML report" in result.stdout
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".report.html.*.tmp"))


def test_report_cli_replace_failure_preserves_existing_output_and_cleans_temp(
    tmp_path, monkeypatch
) -> None:
    store, session_id = analyzed_store(tmp_path)
    output = tmp_path / "report.html"
    original = b"existing report"
    output.write_bytes(original)

    def fail_replace(*args, **kwargs):
        raise OSError("synthetic private write failure")

    monkeypatch.setattr("session_doctor.html.document.os.replace", fail_replace)
    result = invoke_html(store, session_id, output)

    assert result.exit_code == 1
    assert result.stdout.strip() == "Could not write HTML report."
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".report.html.*.tmp"))


def test_report_cli_rejects_symlink_and_unwritable_parent(tmp_path, monkeypatch) -> None:
    store, session_id = analyzed_store(tmp_path)
    target = tmp_path / "target.html"
    target.write_text("target")
    symlink = tmp_path / "link.html"
    symlink.symlink_to(target)

    symlink_result = invoke_html(store, session_id, symlink)
    monkeypatch.setattr("session_doctor.cli_options.os_access_writable", lambda path: False)
    unwritable_result = invoke_html(store, session_id, tmp_path / "unwritable.html")

    assert symlink_result.exit_code == 2
    assert "destination must be a regular file" in symlink_result.stdout
    assert target.read_text() == "target"
    assert unwritable_result.exit_code == 2
    assert "parent directory is not writable" in unwritable_result.stdout


def test_native_three_adapter_and_sidechain_html_smoke(tmp_path) -> None:
    database_path = tmp_path / "native.duckdb"
    for agent in ("codex", "claude", "pi"):
        ingest = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                agent,
                "--source",
                str(FIXTURE_ROOT / agent / "repeated-failure-session.jsonl"),
                "--db",
                str(database_path),
            ],
        )
        assert ingest.exit_code == 0
    topology = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(FIXTURE_ROOT / "claude" / "topology"),
            "--db",
            str(database_path),
        ],
    )
    assert topology.exit_code == 0
    analysis = runner.invoke(app, ["analyze", "--all", "--db", str(database_path)])
    assert analysis.exit_code == 0
    store = DuckDBStore(database_path)
    sessions = store.list_session_summaries()
    sidechain_found = False

    for session in sessions:
        snapshot = store.load_diagnostic_snapshot(session.session_id)
        assert snapshot is not None
        typed_report = build_session_report(snapshot)
        sidechain_found |= snapshot.normalized.session.is_sidechain
        output = tmp_path / f"{session.session_id}.html"
        result = invoke_html(store, session.session_id, output)
        assert result.exit_code == 0
        html = output.read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        assert session.session_id in html
        assert "Source record position does not imply" in html
        assert humanize_status(typed_report.analysis.status) + " analysis" in html
        if typed_report.sequence.evidence_markers:
            assert typed_report.sequence.evidence_markers[0].evidence_id in html
        displayed_evidence = [
            item for section in typed_report.evidence.values() for item in section.items
        ]
        if displayed_evidence:
            assert displayed_evidence[0].evidence_id in html
        for message in snapshot.normalized.messages:
            if message.text is not None and len(message.text) > 20:
                assert message.text not in html
        if snapshot.normalized.session.is_sidechain:
            assert "Sidechain" in html

    assert {session.agent_name for session in sessions} == {"codex", "claude", "pi"}
    assert sidechain_found


def invoke_html(store: DuckDBStore, session_id: str, output: Path):
    return runner.invoke(
        app,
        [
            "report",
            session_id,
            "--db",
            str(store.database_path),
            "--format",
            "html",
            "--output",
            str(output),
        ],
    )


def humanize_status(status: str) -> str:
    return status.replace("_", " ").title()


def analyzed_store(tmp_path) -> tuple[DuckDBStore, str]:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "report.duckdb")
    store.insert_untracked_parsed_bundle(source_for_bundle(), bundle)
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


EXPECTED_H2 = [
    "Diagnostic overview",
    "Scores and contributions",
    "Session sequence",
    "Evidence",
    "Ending, recurrence, and actions",
]


def test_report_help_documents_html_write_contract() -> None:
    result = runner.invoke(app, ["report", "--help"])
    help_text = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Output format:" in help_text
    assert "html." in help_text
    assert "--output" in help_text
    assert "replace atomically" in help_text

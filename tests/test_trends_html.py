from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from test_trends import add_analysis, add_session
from typer.testing import CliRunner

from session_doctor.cli import app
from session_doctor.html import HtmlRenderError, render_trends_html
from session_doctor.store import TABLE_NAMES, DuckDBStore, TrendBucketSize, TrendFilters

runner = CliRunner()


def test_trends_html_is_deterministic_complete_offline_and_cohort_separated(tmp_path) -> None:
    store = populated_store(tmp_path)
    report = store.trends(
        TrendFilters(
            project_path=str(Path.home() / "project"),
            bucket=TrendBucketSize.WEEK,
            periods=4,
        )
    )

    first = render_trends_html(report)
    second = render_trends_html(report)

    assert first == second
    assert first.startswith("<!doctype html>")
    assert first.endswith("</body>\n</html>\n")
    assert first.count("</html>") == 1
    assert '<main id="main-content">' in first
    assert all(
        heading in first
        for heading in (
            "Scope and analysis coverage",
            "Contribution calendars",
            "Trend charts",
            "Judgments and distributions",
            "Recurring patterns",
        )
    )
    assert "~/project" in first
    assert str(Path.home()) not in first
    assert "Top-level calendar" in first
    assert "Sidechain calendar" in first
    assert first.count('class="calendar-cell ') >= 56
    assert 'data-calendar-view="volume"' in first
    assert 'data-calendar-view="risk"' in first
    assert 'data-calendar-view="risk" hidden' not in first
    assert "if (controls)" in first
    assert "Observed date 2026-01-05" in first
    assert "risky sessions 1 of 1 current analyzed" in first
    assert "current-analysis coverage 100.0%" in first
    assert first.count('class="trend-chart"') == 6
    assert "Text alternative: session volume and analysis coverage" in first
    assert "Text alternative: score averages and sample counts" in first
    assert "Text alternative: risky-session rates and denominators" in first
    assert "Current analyzed denominator" in first
    assert "project_scope_required" not in first
    assert "prefers-color-scheme: dark" in first
    assert "@media print" in first
    assert "https://" not in first
    assert "http://" not in first
    assert "fetch(" not in first
    assert "localStorage" not in first
    without_script = first[: first.index("<script>")] + first[first.index("</script>") + 9 :]
    assert 'aria-label="Top-level risk calendar"' in without_script
    assert 'aria-label="Sidechain risk calendar"' in without_script
    assert "risky sessions" in without_script
    assert "Current analyzed denominator" in without_script


def test_trends_html_escapes_hostile_filters(tmp_path) -> None:
    store = populated_store(tmp_path)
    report = store.trends(TrendFilters(project_path=str(Path.home() / "project"), periods=4))
    hostile = '"><img src=x onerror="alert(1)">&'
    hostile_report = replace(
        report,
        filters=replace(report.filters, project_path=hostile, agent_name=hostile),
    )

    html = render_trends_html(hostile_report)

    assert hostile not in html
    assert "&lt;img src=x onerror=" in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_trends_html_empty_window_and_unavailable_rates_are_explicit(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "empty.duckdb")
    add_session(store, "untimed", None, project="/work/project")

    html = render_trends_html(store.trends(TrendFilters(project_path="/work/project")))

    assert "Latest matching session" in html
    assert "No observed-date window is available for this cohort" in html
    assert "No nonempty cohort buckets are available" in html
    assert "Unavailable" in html
    assert "Never analyzed" in html


def test_trends_html_monthly_calendar_preserves_year_boundary(tmp_path) -> None:
    store = populated_store(tmp_path)
    report = store.trends(
        TrendFilters(
            project_path=str(Path.home() / "project"),
            bucket=TrendBucketSize.MONTH,
            periods=3,
        )
    )

    html = render_trends_html(report)

    assert report.window.start == datetime(2025, 11, 1)
    assert report.window.end == datetime(2026, 2, 1)
    assert "Observed date 2025-11-01" in html
    assert "Observed date 2026-01-31" in html
    assert len(report.cohorts.top_level.calendar) == 92
    assert len(report.cohorts.sidechain.calendar) == 92


def test_trends_cli_html_requires_output_and_rejects_output_for_other_formats(tmp_path) -> None:
    store = populated_store(tmp_path)
    missing = runner.invoke(
        app,
        ["trends", "--db", str(store.database_path), "--format", "html"],
    )
    non_html = runner.invoke(
        app,
        [
            "trends",
            "--db",
            str(store.database_path),
            "--format",
            "json",
            "--output",
            str(tmp_path / "trends.html"),
        ],
    )

    assert missing.exit_code == 2
    assert "Missing --output" in missing.stdout
    assert non_html.exit_code == 2
    assert "Invalid --output" in non_html.stdout


def test_trends_cli_html_replaces_one_file_and_keeps_database_read_only(tmp_path) -> None:
    store = populated_store(tmp_path)
    before = {name: store.table_count(name) for name in TABLE_NAMES}
    output = tmp_path / "trends.html"
    output.write_bytes(b"old dashboard")

    result = invoke_html(store, output)

    assert result.exit_code == 0
    assert result.stdout.strip() == f"Wrote HTML trends dashboard: {output}"
    assert output.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "<!doctype html>" not in result.stdout
    assert {name: store.table_count(name) for name in TABLE_NAMES} == before
    assert not list(tmp_path.glob(".trends.html.*.tmp"))


def test_trends_cli_render_failure_preserves_existing_output(tmp_path, monkeypatch) -> None:
    store = populated_store(tmp_path)
    output = tmp_path / "trends.html"
    original = b"existing dashboard"
    output.write_bytes(original)

    def fail_render(*args, **kwargs):
        raise HtmlRenderError("synthetic failure")

    monkeypatch.setattr("session_doctor.cli.render_trends_html", fail_render)
    result = invoke_html(store, output)

    assert result.exit_code == 1
    assert result.stdout.strip() == "Could not write HTML trends dashboard."
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".trends.html.*.tmp"))


def test_trends_help_documents_html_write_contract() -> None:
    result = runner.invoke(app, ["trends", "--help"])
    help_text = " ".join(result.stdout.split())

    assert result.exit_code == 0
    assert "Output format:" in help_text
    assert "html." in help_text
    assert "--output" in help_text
    assert "replace atomically" in help_text


def populated_store(tmp_path) -> DuckDBStore:
    store = DuckDBStore(tmp_path / "trends.duckdb")
    project = str(Path.home() / "project")
    add_session(store, "top-current", datetime(2026, 1, 5, 9), project=project)
    add_session(store, "top-stale", datetime(2026, 1, 13, 9), project=project)
    add_session(store, "top-never", datetime(2026, 1, 13, 12), project=project)
    add_session(
        store,
        "side-current",
        datetime(2026, 1, 28, 9),
        project=project,
        sidechain=True,
    )
    add_analysis(store, "top-current", score=0.8, labels=("tooling_blocked",))
    add_analysis(store, "top-stale", score=0.9, analyzer_version="phase5")
    add_analysis(store, "side-current", score=0.2)
    return store


def invoke_html(store: DuckDBStore, output: Path):
    return runner.invoke(
        app,
        [
            "trends",
            "--db",
            str(store.database_path),
            "--project",
            str(Path.home() / "project"),
            "--periods",
            "4",
            "--format",
            "html",
            "--output",
            str(output),
        ],
    )

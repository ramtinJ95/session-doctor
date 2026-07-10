from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import ANALYZER_VERSION
from session_doctor.cli import app
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    CommandRun,
    FileActivity,
    Session,
    SessionClassification,
    SessionFeature,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import (
    DuckDBStore,
    ProjectFilters,
    TrendBucketSize,
    TrendFilters,
    TrendStatus,
)
from session_doctor.store.aggregate_queries import SCORE_NAMES
from session_doctor.store.trend_models import ScoreAggregate, TrendBucket, TrendMetrics
from session_doctor.store.trend_readers import (
    bucket_intervals,
    build_judgment,
    coverage_difference_too_large,
    judgment_status,
    trend_window,
)
from session_doctor.trend_payload import trend_payload

runner = CliRunner()
FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_trends_aligns_weekly_scope_coverage_and_cohorts(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    add_session(store, "top-current", datetime(2026, 1, 5, 9), project="/work/project")
    add_session(store, "top-stale", datetime(2026, 1, 13, 9), project="/work/project")
    add_session(
        store,
        "side-current",
        datetime(2026, 1, 28, 9),
        project="/work/project",
        sidechain=True,
    )
    add_session(store, "outside", datetime(2025, 12, 1, 9), project="/work/project")
    add_session(store, "untimed", None, project="/work/project")
    add_analysis(store, "top-current", score=0.8, labels=("tooling_blocked",))
    add_analysis(store, "top-stale", score=0.9, analyzer_version="phase5")
    add_analysis(store, "side-current", score=0.2)
    add_analysis(store, "outside", score=0.4)

    report = store.trends(
        TrendFilters(project_path="/work/project", bucket=TrendBucketSize.WEEK, periods=4)
    )

    assert report.window.start == datetime(2026, 1, 5)
    assert report.window.end == datetime(2026, 2, 2)
    assert report.window.latest_session_at == datetime(2026, 1, 28, 9)
    assert report.scope.matching_sessions == 5
    assert report.scope.windowed_sessions == 3
    assert report.scope.outside_window_sessions == 1
    assert report.scope.untimed_sessions == 1
    assert (
        report.scope.matching_analysis.current,
        report.scope.matching_analysis.stale,
        report.scope.matching_analysis.never,
    ) == (3, 1, 1)
    assert {
        row.analyzer_version: row.session_count
        for row in report.scope.matching_analysis.version_counts
    } == {
        "phase5": 1,
        ANALYZER_VERSION: 3,
    }
    assert (
        report.scope.windowed_analysis.current,
        report.scope.windowed_analysis.stale,
        report.scope.windowed_analysis.never,
    ) == (2, 1, 0)

    top_level = report.cohorts.top_level
    assert top_level.totals.sessions == 2
    assert top_level.totals.current_analyzed == 1
    assert top_level.totals.stale_analysis == 1
    assert top_level.totals.current_analysis_coverage == 0.5
    friction = score_for(top_level.totals, "friction_score")
    assert friction.average == pytest.approx(0.8)
    assert friction.sample_count == 1
    assert top_level.totals.risky_session_rate == 1.0
    assert [
        (row.label, row.session_count, row.rate) for row in top_level.totals.classifications
    ] == [("tooling_blocked", 1, 1.0)]
    assert len(top_level.buckets) == 4
    assert [bucket.metrics.sessions for bucket in top_level.buckets] == [1, 1, 0, 0]

    sidechain = report.cohorts.sidechain
    assert sidechain.totals.sessions == 1
    assert sidechain.totals.current_analysis_coverage == 1.0
    assert len(sidechain.buckets) == 4
    assert [bucket.metrics.sessions for bucket in sidechain.buckets] == [0, 0, 0, 1]
    assert [(row.agent_name, row.metrics.sessions) for row in top_level.agents] == [("codex", 2)]
    assert [(row.project_path, row.sessions) for row in report.projects.rows] == [
        ("/work/project", 3)
    ]


def test_trend_window_handles_month_year_and_empty_ranges() -> None:
    monthly = trend_window(datetime(2026, 1, 31, 12), TrendBucketSize.MONTH, 3)
    weekly = trend_window(datetime(2026, 7, 10, 10), TrendBucketSize.WEEK, 12)
    empty = trend_window(None, TrendBucketSize.WEEK, 12)

    assert monthly.start == datetime(2025, 11, 1)
    assert monthly.end == datetime(2026, 2, 1)
    assert [start for start, _ in bucket_intervals(monthly, TrendBucketSize.MONTH, 3)] == [
        datetime(2025, 11, 1),
        datetime(2025, 12, 1),
        datetime(2026, 1, 1),
    ]
    assert weekly.start == datetime(2026, 4, 20)
    assert weekly.end == datetime(2026, 7, 13)
    assert empty.start is None
    assert empty.end is None
    assert bucket_intervals(empty, TrendBucketSize.WEEK, 12) == ()


def test_trends_guarded_judgments_exclude_odd_earliest_bucket(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    first_monday = datetime(2026, 1, 5, 9)
    for index in range(13):
        session_id = f"session-{index:02d}"
        add_session(
            store,
            session_id,
            first_monday + timedelta(weeks=index),
            project="/work/project",
        )
        if index == 0:
            scores = score_values(0.0, 0.0, 0.0, 0.0, 0.0)
        elif index <= 6:
            scores = score_values(0.8, 0.7, 0.4, 0.2, 0.3)
        else:
            scores = score_values(0.4, 0.3, 0.4, 0.4, 0.1)
        add_analysis(store, session_id, scores=scores)

    report = store.trends(
        TrendFilters(project_path="/work/project", bucket=TrendBucketSize.WEEK, periods=13)
    )
    judgments = {row.metric_name: row for row in report.cohorts.top_level.judgments}

    assert judgments["friction_score"].earlier_value == pytest.approx(0.8)
    assert judgments["friction_score"].recent_value == pytest.approx(0.4)
    assert judgments["friction_score"].status is TrendStatus.IMPROVING
    assert judgments["stuckness_score"].status is TrendStatus.IMPROVING
    assert judgments["prompt_clarity_risk"].status is TrendStatus.NO_MATERIAL_CHANGE
    assert judgments["agent_fit_risk"].status is TrendStatus.INCREASING
    assert judgments["project_complexity_signal"].status is TrendStatus.DECREASING
    assert judgments["risky_session_rate"].status is TrendStatus.IMPROVING
    assert all(not judgment.reasons for judgment in judgments.values())
    assert all(judgment.earlier_sample_count == 6 for judgment in judgments.values())
    assert all(judgment.recent_sample_count == 6 for judgment in judgments.values())


def test_trends_without_project_keeps_raw_series_but_blocks_direction(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    start = datetime(2026, 1, 5, 9)
    for index in range(12):
        session_id = f"session-{index:02d}"
        add_session(store, session_id, start + timedelta(weeks=index), project="/work/project")
        add_analysis(store, session_id, score=0.8 if index < 6 else 0.2)

    report = store.trends(TrendFilters(periods=12))

    assert len(report.cohorts.top_level.buckets) == 12
    assert all(
        judgment.status is TrendStatus.INSUFFICIENT_DATA
        and "project_scope_required" in judgment.reasons
        for judgment in report.cohorts.top_level.judgments
    )


def test_trend_judgment_is_session_weighted_and_threshold_inclusive() -> None:
    start = datetime(2026, 1, 5)
    buckets = []
    for index in range(12):
        if index == 0:
            metrics = synthetic_metrics(sessions=5, current=5, score_total=5.0, samples=5)
        elif index < 6:
            metrics = synthetic_metrics(sessions=1, current=1, score_total=0.0, samples=1)
        else:
            metrics = synthetic_metrics(sessions=1, current=1, score_total=0.2, samples=1)
        buckets.append(
            TrendBucket(
                start=start + timedelta(weeks=index),
                end=start + timedelta(weeks=index + 1),
                metrics=metrics,
            )
        )

    judgment = build_judgment("friction_score", tuple(buckets), has_project_scope=True)

    assert judgment.earlier_value == pytest.approx(0.5)
    assert judgment.recent_value == pytest.approx(0.2)
    assert judgment.status is TrendStatus.IMPROVING
    assert judgment_status("friction_score", -0.10, []) is TrendStatus.IMPROVING
    assert judgment_status("friction_score", 0.10, []) is TrendStatus.WORSENING
    assert judgment_status("agent_fit_risk", -0.10, []) is TrendStatus.DECREASING
    assert judgment_status("agent_fit_risk", 0.10, []) is TrendStatus.INCREASING
    assert judgment_status("friction_score", 0.099, []) is TrendStatus.NO_MATERIAL_CHANGE


def test_trend_judgment_reports_density_sample_and_coverage_reasons() -> None:
    start = datetime(2026, 1, 5)
    buckets = tuple(
        TrendBucket(
            start=start + timedelta(weeks=index),
            end=start + timedelta(weeks=index + 1),
            metrics=synthetic_metrics(sessions=1 if index in {0, 1, 6, 7} else 0),
        )
        for index in range(12)
    )

    judgment = build_judgment("friction_score", buckets, has_project_scope=True)

    assert set(judgment.reasons) >= {
        "too_few_nonempty_earlier_buckets",
        "too_few_nonempty_recent_buckets",
        "too_few_earlier_samples",
        "too_few_recent_samples",
        "insufficient_earlier_coverage",
        "insufficient_recent_coverage",
        "insufficient_earlier_sample_coverage",
        "insufficient_recent_sample_coverage",
    }
    assert coverage_difference_too_large(1.0, 0.85) is False
    assert coverage_difference_too_large(1.0, 0.849) is True


def test_trends_all_untimed_preserves_scope_and_null_window(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    add_session(store, "untimed-current", None, project="/work/project")
    add_session(store, "untimed-missing", None, project="/work/project")
    add_analysis(store, "untimed-current", score=0.4)

    payload = trend_payload(store.trends(TrendFilters(project_path="/work/project", periods=12)))

    assert payload["window"] == {
        "start": None,
        "end": None,
        "anchor": "none",
        "latest_session_at": None,
    }
    scope = cast("dict[str, Any]", payload["scope"])
    assert scope["matching_sessions"] == 2
    assert scope["windowed_sessions"] == 0
    assert scope["untimed_sessions"] == 2
    cohorts = cast("dict[str, Any]", payload["cohorts"])
    top_level = cast("dict[str, Any]", cohorts["top_level"])
    assert top_level["buckets"] == []


def test_trends_cli_json_and_terminal_are_read_only(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    add_session(store, "session-a", datetime(2026, 3, 2, 9), project=str(Path.home() / "project"))
    add_analysis(store, "session-a", score=0.7, labels=("user_stuck",))
    analysis_count = store.table_count("analysis_runs")

    json_result = runner.invoke(
        app,
        [
            "trends",
            "--db",
            str(database_path),
            "--project",
            str(Path.home() / "project"),
            "--periods",
            "3",
            "--format",
            "json",
        ],
    )
    terminal_result = runner.invoke(
        app,
        ["trends", "--db", str(database_path), "--bucket", "month", "--periods", "3"],
    )

    assert json_result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(json_result.stdout))
    assert payload["filters"] == {
        "agent": None,
        "project": "~/project",
        "bucket": "week",
        "periods": 3,
        "limit": 10,
    }
    assert set(payload) == {
        "filters",
        "window",
        "scope",
        "cohorts",
        "projects",
        "unknown_project_sessions",
        "recurring_patterns",
    }
    assert terminal_result.exit_code == 0
    assert "Session trends" in terminal_result.stdout
    assert "Top-level buckets" in terminal_result.stdout
    assert "Top-level judgments" in terminal_result.stdout
    assert store.table_count("analysis_runs") == analysis_count
    assert not (tmp_path / "artifacts").exists()


def test_projects_list_keeps_exact_paths_unknowns_and_analysis_versions(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    home_project = str(Path.home() / "project")
    add_session(store, "root-a", datetime(2026, 1, 1, 9), project=home_project)
    add_session(store, "root-b", datetime(2026, 1, 2, 9), project=home_project)
    add_session(
        store,
        "nested",
        datetime(2026, 1, 3, 9),
        project=f"{home_project}/nested",
        sidechain=True,
    )
    add_session(
        store, "pi-project", datetime(2026, 1, 4, 9), project="/work/pi", agent=AgentName.PI
    )
    add_session(store, "unknown", None, project="")
    add_analysis(store, "root-a", score=0.4)
    add_analysis(store, "root-b", score=0.4, analyzer_version="phase5")
    add_analysis(store, "nested", score=0.4)

    report = store.projects(ProjectFilters(limit=10))
    json_result = runner.invoke(
        app,
        ["projects", "list", "--db", str(database_path), "--format", "json"],
    )
    pi_result = runner.invoke(
        app,
        [
            "projects",
            "list",
            "--db",
            str(database_path),
            "--agent",
            "pi",
            "--format",
            "json",
        ],
    )

    assert [(row.project_path, row.sessions) for row in report.observations.rows] == [
        (home_project, 2),
        ("/work/pi", 1),
        (f"{home_project}/nested", 1),
    ]
    assert report.observations.unknown_sessions == 1
    assert json_result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(json_result.stdout))
    assert payload["unknown_project_sessions"] == 1
    projects = cast("list[dict[str, Any]]", payload["projects"])
    assert projects[0]["project"] == "~/project"
    assert projects[0]["analysis"] == {
        "current": 1,
        "stale": 1,
        "never": 0,
        "version_counts": {"phase5": 1, ANALYZER_VERSION: 1},
    }
    assert pi_result.exit_code == 0
    pi_payload = cast("dict[str, Any]", json.loads(pi_result.stdout))
    assert [row["project"] for row in pi_payload["projects"]] == ["/work/pi"]


def test_recurring_patterns_require_distinct_valid_filtered_root_families(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    recurring_command = "tool --api-key TOP_SECRET"
    recurring_file = str(Path.home() / "project" / "src" / "app.py")
    non_problematic_file = str(Path.home() / "project" / "src" / "safe.py")
    add_pattern_session(
        store,
        "root-a",
        datetime(2026, 1, 5, 9),
        project="/work/project",
        agent=AgentName.CODEX,
        files=(non_problematic_file,),
    )
    add_pattern_session(
        store,
        "side-a",
        datetime(2026, 1, 6, 9),
        project="/work/project",
        agent=AgentName.CODEX,
        parent="root-a",
        commands=(recurring_command, recurring_command, "one-family-command"),
        cancelled_commands={recurring_command},
        tool_errors=(("bash", "shared-output-hash"), ("read", "shared-output-hash")),
        unknown_tool_error_hash="unknown-output-hash",
        files=(recurring_file, "unresolved.py"),
    )
    add_pattern_session(
        store,
        "root-b",
        datetime(2026, 1, 12, 9),
        project="/work/project",
        agent=AgentName.PI,
        files=(non_problematic_file,),
    )
    add_pattern_session(
        store,
        "nested-a",
        datetime(2026, 1, 7, 9),
        project="/work/project/nested",
        agent=AgentName.CODEX,
        parent="side-a",
        commands=(recurring_command, "one-family-command"),
    )
    add_pattern_session(
        store,
        "side-b",
        datetime(2026, 1, 13, 9),
        project="/work/project/subdir",
        agent=AgentName.PI,
        parent="root-b",
        commands=(recurring_command,),
        tool_errors=(("bash", "shared-output-hash"), ("read", "shared-output-hash")),
        unknown_tool_error_hash="unknown-output-hash",
        files=(recurring_file, "unresolved.py"),
    )
    add_pattern_session(
        store,
        "orphan",
        datetime(2026, 1, 14, 9),
        project="/work/project",
        parent="missing-parent",
        commands=(recurring_command,),
    )
    add_pattern_session(
        store,
        "cycle-a",
        datetime(2026, 1, 15, 9),
        project="/work/project",
        parent="cycle-b",
    )
    add_pattern_session(
        store,
        "cycle-b",
        datetime(2026, 1, 16, 9),
        project="/work/project",
        parent="cycle-a",
    )
    add_pattern_session(
        store,
        "cross-agent",
        datetime(2026, 1, 17, 9),
        project="/work/project",
        agent=AgentName.CODEX,
        parent="root-b",
    )
    add_pattern_session(
        store,
        "outside-member",
        datetime(2026, 1, 18, 9),
        project="/other",
        parent="root-a",
        commands=(recurring_command,),
    )
    add_pattern_session(
        store,
        "old-root",
        datetime(2025, 11, 3, 9),
        project="/work/project",
    )
    add_pattern_session(
        store,
        "old-side",
        datetime(2026, 1, 19, 9),
        project="/work/project",
        parent="old-root",
        commands=(recurring_command,),
    )
    add_pattern_session(
        store,
        "nonmatching-root",
        datetime(2026, 1, 8, 9),
        project="/other",
    )
    add_pattern_session(
        store,
        "matching-child",
        datetime(2026, 1, 9, 9),
        project="/work/project",
        parent="nonmatching-root",
        commands=(recurring_command,),
    )
    add_analysis(store, "side-a", score=0.8)
    add_analysis(store, "side-b", score=0.8, analyzer_version="phase5")

    report = store.trends(TrendFilters(project_path="/work/project", periods=4, limit=10))
    payload = trend_payload(report)
    patterns = report.recurring_patterns

    assert patterns.family_exclusions.orphan_parent == 1
    assert patterns.family_exclusions.cycle == 2
    assert patterns.family_exclusions.cross_agent_parent == 1
    assert len(patterns.failed_commands) == 1
    command_pattern = patterns.failed_commands[0]
    assert command_pattern.command == "tool --api-key <redacted>"
    assert command_pattern.evidence.event_count == 4
    assert command_pattern.evidence.session_count == 3
    assert command_pattern.evidence.root_family_count == 2
    assert command_pattern.evidence.top_level_session_count == 0
    assert command_pattern.evidence.sidechain_session_count == 3
    assert command_pattern.evidence.agents == ("codex", "pi")
    assert command_pattern.evidence.active_bucket_count == 2
    assert {row.tool_name for row in patterns.failed_tool_results} == {"bash", "read", "unknown"}
    assert len({row.fingerprint_id for row in patterns.failed_tool_results}) == 3
    assert all(
        "shared-output-hash" not in row.fingerprint_id for row in patterns.failed_tool_results
    )
    assert len(patterns.problematic_files) == 1
    assert patterns.problematic_files[0].path == recurring_file
    limited = store.trends(
        TrendFilters(project_path="/work/project", periods=4, limit=1)
    ).recurring_patterns
    assert len(limited.failed_commands) == 1
    assert len(limited.failed_tool_results) == 1
    assert len(limited.problematic_files) == 1
    serialized = json.dumps(payload, sort_keys=True)
    assert "TOP_SECRET" not in serialized
    assert "shared-output-hash" not in serialized
    assert "unknown-output-hash" not in serialized
    assert str(Path.home()) not in serialized
    assert "one-family-command" not in serialized
    terminal_result = runner.invoke(
        app,
        [
            "trends",
            "--db",
            str(store.database_path),
            "--project",
            "/work/project",
            "--periods",
            "4",
        ],
    )
    assert terminal_result.exit_code == 0
    assert "Recurring failed commands" in terminal_result.stdout


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--bucket", "day"], "Invalid --bucket"),
        (["--periods", "0"], "Invalid --periods"),
        (["--periods", "121"], "Invalid --periods"),
        (["--limit", "0"], "Invalid --limit"),
        (["--agent", "unknown"], "Unsupported --agent"),
        (["--format", "yaml"], "Invalid --format"),
    ],
)
def test_trends_rejects_invalid_options(tmp_path, arguments, message) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    DuckDBStore(database_path).initialize()

    result = runner.invoke(app, ["trends", "--db", str(database_path), *arguments])

    assert result.exit_code == 2
    assert message in result.stdout


def test_trends_rejects_missing_database(tmp_path) -> None:
    result = runner.invoke(app, ["trends", "--db", str(tmp_path / "missing.duckdb")])

    assert result.exit_code == 1
    assert "Database does not exist" in result.stdout


def test_native_three_adapter_flow_reaches_trends_and_projects(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    for agent in ("codex", "claude", "pi"):
        ingest_result = runner.invoke(
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
        assert ingest_result.exit_code == 0

    analysis_result = runner.invoke(
        app,
        ["analyze", "--all", "--db", str(database_path), "--format", "json"],
    )
    weekly_result = runner.invoke(
        app,
        ["trends", "--db", str(database_path), "--format", "json"],
    )
    monthly_result = runner.invoke(
        app,
        [
            "trends",
            "--db",
            str(database_path),
            "--project",
            "/tmp/session-doctor",
            "--bucket",
            "month",
            "--periods",
            "3",
        ],
    )
    projects_result = runner.invoke(
        app,
        ["projects", "list", "--db", str(database_path), "--format", "json"],
    )

    assert analysis_result.exit_code == 0
    analysis_payload = cast("dict[str, Any]", json.loads(analysis_result.stdout))
    assert analysis_payload["counts"] == {
        "matching": 3,
        "selected": 3,
        "succeeded": 3,
        "skipped": 0,
        "failed": 0,
    }
    assert weekly_result.exit_code == 0
    weekly_payload = cast("dict[str, Any]", json.loads(weekly_result.stdout))
    assert weekly_payload["scope"]["matching_sessions"] == 3
    top_level = cast("dict[str, Any]", weekly_payload["cohorts"]["top_level"])
    assert {row["agent"] for row in top_level["agents"]} == {"codex", "claude", "pi"}
    assert all(judgment["status"] == "insufficient_data" for judgment in top_level["judgments"])
    assert monthly_result.exit_code == 0
    assert "Session trends" in monthly_result.stdout
    assert projects_result.exit_code == 0
    project_payload = cast("dict[str, Any]", json.loads(projects_result.stdout))
    assert project_payload["unknown_project_sessions"] == 0
    assert project_payload["projects"] == [
        {
            "project": "/tmp/session-doctor",
            "sessions": 3,
            "top_level_sessions": 3,
            "sidechain_sessions": 0,
            "analysis": {
                "current": 3,
                "stale": 0,
                "never": 0,
                "version_counts": {ANALYZER_VERSION: 3},
            },
            "first_session_at": "2026-05-06T09:00:00",
            "latest_session_at": "2026-05-08T12:00:00",
            "agents": ["claude", "codex", "pi"],
        }
    ]
    assert not (tmp_path / "artifacts").exists()


def add_session(
    store: DuckDBStore,
    session_id: str,
    started_at: datetime | None,
    *,
    project: str,
    sidechain: bool = False,
    agent: AgentName = AgentName.CODEX,
    parent: str | None = None,
) -> None:
    source = SessionSource(
        source_id=f"source-{session_id}",
        agent_name=agent,
        source_path=f"/tmp/{session_id}.jsonl",
    )
    store.insert_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=Session(
                session_id=session_id,
                source_id=source.source_id,
                agent_name=agent,
                project_path=project,
                started_at=started_at,
                is_sidechain=sidechain,
                parent_session_id=parent,
            )
        ),
    )


def add_analysis(
    store: DuckDBStore,
    session_id: str,
    *,
    score: float | None = None,
    scores: dict[str, float] | None = None,
    labels: tuple[str, ...] = (),
    analyzer_version: str = ANALYZER_VERSION,
) -> None:
    score_map = scores or {name: score for name in SCORE_NAMES if score is not None}
    analysis_run = AnalysisRun(
        analysis_run_id=f"analysis-{session_id}-{analyzer_version}",
        session_id=session_id,
        analyzer_version=analyzer_version,
        started_at=datetime(2026, 6, 1, 8),
        completed_at=datetime(2026, 6, 1, 9),
    )
    features = [
        SessionFeature(
            session_feature_id=f"feature-{session_id}-{name}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            feature_name=name,
            feature_value=f"{value:.3f}",
            score=value,
        )
        for name, value in score_map.items()
        if value is not None
    ]
    classifications = [
        SessionClassification(
            session_classification_id=f"classification-{session_id}-{label}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            label=label,
            score=0.8,
            confidence=0.8,
            evidence_summary=f"Synthetic {label} evidence",
        )
        for label in labels
    ]
    store.replace_analysis_rows(analysis_run, [], features, classifications)


def score_values(
    friction: float,
    stuckness: float,
    prompt: float,
    fit: float,
    complexity: float,
) -> dict[str, float]:
    return {
        "friction_score": friction,
        "stuckness_score": stuckness,
        "prompt_clarity_risk": prompt,
        "agent_fit_risk": fit,
        "project_complexity_signal": complexity,
    }


def score_for(metrics, metric_name: str):
    return next(score for score in metrics.scores if score.metric_name == metric_name)


def add_pattern_session(
    store: DuckDBStore,
    session_id: str,
    started_at: datetime,
    *,
    project: str,
    agent: AgentName = AgentName.CODEX,
    parent: str | None = None,
    commands: tuple[str, ...] = (),
    cancelled_commands: set[str] | None = None,
    tool_errors: tuple[tuple[str, str], ...] = (),
    unknown_tool_error_hash: str | None = None,
    files: tuple[str, ...] = (),
) -> None:
    source = SessionSource(
        source_id=f"source-{session_id}",
        agent_name=agent,
        source_path=f"/tmp/{session_id}.jsonl",
    )
    tool_calls = [
        ToolCall(
            tool_call_id=f"tool-call-{session_id}-{index}",
            session_id=session_id,
            name=tool_name,
            timestamp=started_at + timedelta(minutes=index),
        )
        for index, (tool_name, _) in enumerate(tool_errors)
    ]
    tool_results = [
        ToolResult(
            tool_result_id=f"tool-result-{session_id}-{index}",
            session_id=session_id,
            tool_call_id=tool_calls[index].tool_call_id,
            timestamp=started_at + timedelta(minutes=index),
            is_error=True,
            output_hash=output_hash,
        )
        for index, (_, output_hash) in enumerate(tool_errors)
    ]
    if unknown_tool_error_hash is not None:
        tool_results.append(
            ToolResult(
                tool_result_id=f"tool-result-{session_id}-unknown",
                session_id=session_id,
                timestamp=started_at,
                is_error=True,
                output_hash=unknown_tool_error_hash,
            )
        )
    command_rows = [
        CommandRun(
            command_run_id=f"command-{session_id}-{index}",
            session_id=session_id,
            command=command,
            started_at=started_at + timedelta(minutes=index),
            exit_code=None if command in (cancelled_commands or set()) else 1,
            metadata={"cancelled": True} if command in (cancelled_commands or set()) else {},
        )
        for index, command in enumerate(commands)
    ]
    file_rows = [
        FileActivity(
            file_activity_id=f"file-{session_id}-{index}",
            session_id=session_id,
            path=path,
            operation="edit",
            timestamp=started_at + timedelta(minutes=index),
        )
        for index, path in enumerate(files)
    ]
    store.insert_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=Session(
                session_id=session_id,
                source_id=source.source_id,
                agent_name=agent,
                parent_session_id=parent,
                started_at=started_at,
                project_path=project,
                is_sidechain=parent is not None,
            ),
            command_runs=command_rows,
            tool_calls=tool_calls,
            tool_results=tool_results,
            file_activities=file_rows,
        ),
    )


def synthetic_metrics(
    *,
    sessions: int,
    current: int = 0,
    score_total: float = 0.0,
    samples: int = 0,
) -> TrendMetrics:
    return TrendMetrics(
        sessions=sessions,
        current_analyzed=current,
        stale_analysis=0,
        never_analyzed=sessions - current,
        scores=tuple(
            ScoreAggregate(
                metric_name=name,
                total=score_total if name == "friction_score" else 0.0,
                sample_count=samples if name == "friction_score" else 0,
            )
            for name in SCORE_NAMES
        ),
        classifications=(),
        risky_sessions=0,
    )

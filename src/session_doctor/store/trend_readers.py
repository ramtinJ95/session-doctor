from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from session_doctor.analysis import ANALYZER_VERSION

from .aggregate_queries import (
    RISK_SCORE_THRESHOLD,
    SCORE_NAMES,
    base_sessions_cte,
    label_groups_sql,
    latest_analysis_sql,
    score_features_sql,
)
from .analysis_readers import AnalysisCompatibility, analysis_compatibility
from .connection import read_connection
from .trend_models import (
    AgentObservation,
    AnalysisCompatibilityCounts,
    AnalyzerVersionCount,
    ClassificationAggregate,
    DailyCalendarCell,
    ProjectObservation,
    ProjectObservations,
    ScoreAggregate,
    TrendBucket,
    TrendBucketSize,
    TrendCohort,
    TrendCohorts,
    TrendFilters,
    TrendJudgment,
    TrendMetrics,
    TrendReport,
    TrendScope,
    TrendStatus,
    TrendWindow,
)

MATERIALITY_THRESHOLD = 0.10
MIN_COMPARISON_SAMPLES = 6
MIN_COVERAGE = 0.80
MAX_COVERAGE_DIFFERENCE = 0.15
COMPARISON_METHOD = "two_window_session_weighted_mean"
NEUTRAL_SIGNAL_NAMES = {"agent_fit_risk", "project_complexity_signal"}


@dataclass(frozen=True)
class SessionTrendRow:
    session_id: str
    agent_name: str
    parent_session_id: str | None
    started_at: datetime | None
    is_sidechain: bool
    project_path: str | None
    analyzer_version: str | None
    compatibility: AnalysisCompatibility
    scores: tuple[float | None, ...]
    labels: tuple[str, ...]
    is_risky: bool


def read_trends(database_path: Path, filters: TrendFilters) -> TrendReport:
    with read_connection(database_path) as connection:
        rows = session_trend_rows(connection, filters)
        return build_trend_report(connection, filters, rows)


def build_trend_report(
    connection: duckdb.DuckDBPyConnection,
    filters: TrendFilters,
    rows: tuple[SessionTrendRow, ...],
) -> TrendReport:
    from .pattern_readers import read_recurring_patterns

    latest_session_at = max(
        (row.started_at for row in rows if row.started_at is not None),
        default=None,
    )
    window = trend_window(latest_session_at, filters.bucket, filters.periods)
    timed_rows = tuple(row for row in rows if row.started_at is not None)
    windowed_rows = tuple(
        row
        for row in timed_rows
        if window.start is not None
        and window.end is not None
        and row.started_at is not None
        and window.start <= row.started_at < window.end
    )
    scope = TrendScope(
        matching_sessions=len(rows),
        windowed_sessions=len(windowed_rows),
        outside_window_sessions=len(timed_rows) - len(windowed_rows),
        untimed_sessions=len(rows) - len(timed_rows),
        matching_analysis=compatibility_counts(rows),
        windowed_analysis=compatibility_counts(windowed_rows),
    )
    intervals = bucket_intervals(window, filters.bucket, filters.periods)
    top_level_rows = tuple(row for row in windowed_rows if not row.is_sidechain)
    sidechain_rows = tuple(row for row in windowed_rows if row.is_sidechain)
    return TrendReport(
        filters=filters,
        window=window,
        scope=scope,
        cohorts=TrendCohorts(
            top_level=build_cohort(top_level_rows, intervals, window, filters),
            sidechain=build_cohort(sidechain_rows, intervals, window, filters),
        ),
        projects=project_observations(windowed_rows, filters.limit),
        recurring_patterns=read_recurring_patterns(connection, filters, window, rows),
    )


def session_trend_rows(
    connection: duckdb.DuckDBPyConnection,
    filters: TrendFilters,
) -> tuple[SessionTrendRow, ...]:
    base_cte, params = base_sessions_cte(filters)
    rows = connection.execute(
        f"""
        WITH {base_cte},
        latest_analysis AS ({latest_analysis_sql()}),
        eligible_analysis AS (
            SELECT * FROM latest_analysis WHERE analyzer_version = ?
        ),
        score_features AS ({score_features_sql()}),
        label_groups AS ({label_groups_sql()})
        SELECT
            b.session_id,
            b.agent_name,
            b.parent_session_id,
            b.started_at,
            b.is_sidechain,
            COALESCE(NULLIF(b.project_path, ''), NULLIF(b.cwd, '')) AS project_path,
            la.analyzer_version,
            sf.friction_score,
            sf.stuckness_score,
            sf.prompt_clarity_risk,
            sf.agent_fit_risk,
            sf.project_complexity_signal,
            COALESCE(lg.labels, ''),
            COALESCE(lg.risk_label_count, 0)
        FROM base_sessions AS b
        LEFT JOIN latest_analysis AS la ON la.session_id = b.session_id
        LEFT JOIN eligible_analysis AS ea ON ea.session_id = b.session_id
        LEFT JOIN score_features AS sf ON sf.session_id = ea.session_id
        LEFT JOIN label_groups AS lg ON lg.session_id = ea.session_id
        ORDER BY b.started_at ASC NULLS LAST, b.session_id ASC
        """,
        [*params, ANALYZER_VERSION],
    ).fetchall()
    return tuple(session_trend_row(row) for row in rows)


def session_trend_row(row: tuple[object, ...]) -> SessionTrendRow:
    analyzer_version = str(row[6]) if row[6] is not None else None
    scores = tuple(optional_float(value) for value in row[7:12])
    labels = tuple(label for label in str(row[12]).split(",") if label)
    compatibility = analysis_compatibility(analyzer_version)
    max_score = max((score for score in scores if score is not None), default=0.0)
    return SessionTrendRow(
        session_id=str(row[0]),
        agent_name=str(row[1]),
        parent_session_id=str(row[2]) if row[2] is not None else None,
        started_at=row[3] if isinstance(row[3], datetime) else None,
        is_sidechain=bool(row[4]),
        project_path=str(row[5]) if row[5] is not None else None,
        analyzer_version=analyzer_version,
        compatibility=compatibility,
        scores=scores,
        labels=labels,
        is_risky=(
            compatibility is AnalysisCompatibility.CURRENT
            and (int(str(row[13])) > 0 or max_score >= RISK_SCORE_THRESHOLD)
        ),
    )


def trend_window(
    latest_session_at: datetime | None,
    bucket_size: TrendBucketSize,
    periods: int,
) -> TrendWindow:
    if latest_session_at is None:
        return TrendWindow(start=None, end=None, latest_session_at=None)
    anchor_start = bucket_start(latest_session_at, bucket_size)
    return TrendWindow(
        start=add_buckets(anchor_start, bucket_size, -(periods - 1)),
        end=add_buckets(anchor_start, bucket_size, 1),
        latest_session_at=latest_session_at,
    )


def bucket_start(value: datetime, bucket_size: TrendBucketSize) -> datetime:
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket_size is TrendBucketSize.WEEK:
        return midnight - timedelta(days=midnight.weekday())
    return midnight.replace(day=1)


def add_buckets(value: datetime, bucket_size: TrendBucketSize, count: int) -> datetime:
    if bucket_size is TrendBucketSize.WEEK:
        return value + timedelta(weeks=count)
    month_index = value.year * 12 + value.month - 1 + count
    year, zero_based_month = divmod(month_index, 12)
    return value.replace(year=year, month=zero_based_month + 1, day=1)


def bucket_intervals(
    window: TrendWindow,
    bucket_size: TrendBucketSize,
    periods: int,
) -> tuple[tuple[datetime, datetime], ...]:
    if window.start is None:
        return ()
    return tuple(
        (
            add_buckets(window.start, bucket_size, index),
            add_buckets(window.start, bucket_size, index + 1),
        )
        for index in range(periods)
    )


def compatibility_counts(rows: tuple[SessionTrendRow, ...]) -> AnalysisCompatibilityCounts:
    compatibility = Counter(row.compatibility for row in rows)
    versions = Counter(row.analyzer_version for row in rows if row.analyzer_version is not None)
    return AnalysisCompatibilityCounts(
        current=compatibility[AnalysisCompatibility.CURRENT],
        stale=compatibility[AnalysisCompatibility.STALE],
        never=compatibility[AnalysisCompatibility.MISSING],
        version_counts=tuple(
            AnalyzerVersionCount(version, count) for version, count in sorted(versions.items())
        ),
    )


def build_cohort(
    rows: tuple[SessionTrendRow, ...],
    intervals: tuple[tuple[datetime, datetime], ...],
    window: TrendWindow,
    filters: TrendFilters,
) -> TrendCohort:
    totals = metrics_for_rows(rows)
    buckets = ()
    if rows:
        buckets = tuple(
            TrendBucket(
                start=start,
                end=end,
                metrics=metrics_for_rows(
                    tuple(
                        row
                        for row in rows
                        if row.started_at is not None and start <= row.started_at < end
                    )
                ),
            )
            for start, end in intervals
        )
    judgments = tuple(
        build_judgment(metric_name, buckets, filters.project_path is not None)
        for metric_name in (*SCORE_NAMES, "risky_session_rate")
    )
    agents = tuple(
        AgentObservation(
            agent_name=agent_name,
            metrics=metrics_for_rows(tuple(row for row in rows if row.agent_name == agent_name)),
        )
        for agent_name in sorted({row.agent_name for row in rows})
    )
    return TrendCohort(
        totals=totals,
        buckets=buckets,
        calendar=daily_calendar(rows, window),
        judgments=judgments,
        agents=agents,
    )


def daily_calendar(
    rows: tuple[SessionTrendRow, ...],
    window: TrendWindow,
) -> tuple[DailyCalendarCell, ...]:
    if window.start is None or window.end is None:
        return ()
    rows_by_date: dict[date, list[SessionTrendRow]] = {}
    for row in rows:
        if row.started_at is not None:
            rows_by_date.setdefault(row.started_at.date(), []).append(row)
    cells: list[DailyCalendarCell] = []
    start = window.start
    while start < window.end:
        end = start + timedelta(days=1)
        metrics = metrics_for_rows(tuple(rows_by_date.get(start.date(), ())))
        cells.append(
            DailyCalendarCell(
                observed_date=start.date(),
                start=start,
                end=end,
                sessions=metrics.sessions,
                current_analyzed=metrics.current_analyzed,
                stale_analysis=metrics.stale_analysis,
                never_analyzed=metrics.never_analyzed,
                risky_sessions=metrics.risky_sessions,
            )
        )
        start = end
    return tuple(cells)


def project_observations(
    rows: tuple[SessionTrendRow, ...],
    limit: int,
) -> ProjectObservations:
    grouped: dict[str, list[SessionTrendRow]] = {}
    unknown_sessions = 0
    for row in rows:
        if row.project_path is None:
            unknown_sessions += 1
            continue
        grouped.setdefault(row.project_path, []).append(row)
    observations = [
        project_observation(path, tuple(project_rows)) for path, project_rows in grouped.items()
    ]
    observations.sort(key=lambda row: row.project_path)
    observations.sort(
        key=lambda row: row.latest_session_at or datetime.min,
        reverse=True,
    )
    observations.sort(key=lambda row: row.sessions, reverse=True)
    return ProjectObservations(rows=tuple(observations[:limit]), unknown_sessions=unknown_sessions)


def project_observation(
    project_path: str,
    rows: tuple[SessionTrendRow, ...],
) -> ProjectObservation:
    timestamps = [row.started_at for row in rows if row.started_at is not None]
    return ProjectObservation(
        project_path=project_path,
        sessions=len(rows),
        top_level_sessions=sum(not row.is_sidechain for row in rows),
        sidechain_sessions=sum(row.is_sidechain for row in rows),
        analysis=compatibility_counts(rows),
        first_session_at=min(timestamps, default=None),
        latest_session_at=max(timestamps, default=None),
        agents=tuple(sorted({row.agent_name for row in rows})),
    )


def metrics_for_rows(rows: tuple[SessionTrendRow, ...]) -> TrendMetrics:
    compatibility = Counter(row.compatibility for row in rows)
    score_aggregates = []
    for score_index, metric_name in enumerate(SCORE_NAMES):
        values = [row.scores[score_index] for row in rows if row.scores[score_index] is not None]
        score_aggregates.append(
            ScoreAggregate(
                metric_name=metric_name,
                total=sum(value for value in values if value is not None),
                sample_count=len(values),
            )
        )
    label_counts = Counter(label for row in rows for label in row.labels)
    current_analyzed = compatibility[AnalysisCompatibility.CURRENT]
    classifications = tuple(
        ClassificationAggregate(
            label=label,
            session_count=count,
            rate=count / current_analyzed if current_analyzed else None,
        )
        for label, count in sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return TrendMetrics(
        sessions=len(rows),
        current_analyzed=current_analyzed,
        stale_analysis=compatibility[AnalysisCompatibility.STALE],
        never_analyzed=compatibility[AnalysisCompatibility.MISSING],
        scores=tuple(score_aggregates),
        classifications=classifications,
        risky_sessions=sum(row.is_risky for row in rows),
    )


def build_judgment(
    metric_name: str,
    buckets: tuple[TrendBucket, ...],
    has_project_scope: bool,
) -> TrendJudgment:
    half_size = len(buckets) // 2
    comparison_buckets = buckets[-(half_size * 2) :] if half_size else ()
    earlier_buckets = comparison_buckets[:half_size]
    recent_buckets = comparison_buckets[half_size:]
    earlier = comparison_values(metric_name, earlier_buckets)
    recent = comparison_values(metric_name, recent_buckets)
    reasons: list[str] = []
    if not has_project_scope:
        reasons.append("project_scope_required")
    if half_size < 3:
        reasons.append("too_few_comparison_buckets")
    else:
        minimum_nonempty = max(3, math.ceil(half_size / 2))
        if earlier.nonempty_buckets < minimum_nonempty:
            reasons.append("too_few_nonempty_earlier_buckets")
        if recent.nonempty_buckets < minimum_nonempty:
            reasons.append("too_few_nonempty_recent_buckets")
    if earlier.sample_count < MIN_COMPARISON_SAMPLES:
        reasons.append("too_few_earlier_samples")
    if recent.sample_count < MIN_COMPARISON_SAMPLES:
        reasons.append("too_few_recent_samples")
    if earlier.current_coverage is None or earlier.current_coverage < MIN_COVERAGE:
        reasons.append("insufficient_earlier_coverage")
    if recent.current_coverage is None or recent.current_coverage < MIN_COVERAGE:
        reasons.append("insufficient_recent_coverage")
    if metric_name != "risky_session_rate":
        if earlier.sample_coverage is None or earlier.sample_coverage < MIN_COVERAGE:
            reasons.append("insufficient_earlier_sample_coverage")
        if recent.sample_coverage is None or recent.sample_coverage < MIN_COVERAGE:
            reasons.append("insufficient_recent_sample_coverage")
    if coverage_difference_too_large(earlier.current_coverage, recent.current_coverage):
        reasons.append("coverage_difference_too_large")
    if metric_name != "risky_session_rate" and coverage_difference_too_large(
        earlier.sample_coverage, recent.sample_coverage
    ):
        reasons.append("coverage_difference_too_large")
    reasons = list(dict.fromkeys(reasons))
    delta = (
        recent.value - earlier.value
        if recent.value is not None and earlier.value is not None
        else None
    )
    status = judgment_status(metric_name, delta, reasons)
    return TrendJudgment(
        metric_name=metric_name,
        status=status,
        earlier_value=earlier.value,
        recent_value=recent.value,
        delta=delta,
        earlier_sample_count=earlier.sample_count,
        recent_sample_count=recent.sample_count,
        earlier_nonempty_buckets=earlier.nonempty_buckets,
        recent_nonempty_buckets=recent.nonempty_buckets,
        earlier_current_analysis_coverage=earlier.current_coverage,
        recent_current_analysis_coverage=recent.current_coverage,
        earlier_sample_coverage=earlier.sample_coverage,
        recent_sample_coverage=recent.sample_coverage,
        threshold=MATERIALITY_THRESHOLD,
        comparison_method=COMPARISON_METHOD,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class ComparisonValues:
    value: float | None
    sample_count: int
    nonempty_buckets: int
    current_coverage: float | None
    sample_coverage: float | None


def comparison_values(
    metric_name: str,
    buckets: tuple[TrendBucket, ...],
) -> ComparisonValues:
    sessions = sum(bucket.metrics.sessions for bucket in buckets)
    current = sum(bucket.metrics.current_analyzed for bucket in buckets)
    nonempty = sum(bucket.metrics.sessions > 0 for bucket in buckets)
    if metric_name == "risky_session_rate":
        risky = sum(bucket.metrics.risky_sessions for bucket in buckets)
        value = risky / current if current else None
        sample_count = current
    else:
        score_rows = [
            next(score for score in bucket.metrics.scores if score.metric_name == metric_name)
            for bucket in buckets
        ]
        sample_count = sum(score.sample_count for score in score_rows)
        score_total = sum(score.total for score in score_rows)
        value = score_total / sample_count if sample_count else None
    current_coverage = current / sessions if sessions else None
    sample_coverage = sample_count / sessions if sessions else None
    return ComparisonValues(
        value=value,
        sample_count=sample_count,
        nonempty_buckets=nonempty,
        current_coverage=current_coverage,
        sample_coverage=sample_coverage,
    )


def coverage_difference_too_large(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    difference = abs(right - left)
    return difference > MAX_COVERAGE_DIFFERENCE and not math.isclose(
        difference, MAX_COVERAGE_DIFFERENCE, abs_tol=1e-12
    )


def judgment_status(
    metric_name: str,
    delta: float | None,
    reasons: list[str],
) -> TrendStatus:
    if reasons or delta is None:
        return TrendStatus.INSUFFICIENT_DATA
    material = abs(delta) > MATERIALITY_THRESHOLD or math.isclose(
        abs(delta), MATERIALITY_THRESHOLD, abs_tol=1e-12
    )
    if not material:
        return TrendStatus.NO_MATERIAL_CHANGE
    if metric_name in NEUTRAL_SIGNAL_NAMES:
        return TrendStatus.INCREASING if delta > 0 else TrendStatus.DECREASING
    return TrendStatus.WORSENING if delta > 0 else TrendStatus.IMPROVING


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(str(value))

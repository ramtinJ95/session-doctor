from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

from session_doctor.privacy import redact_home
from session_doctor.store.aggregate_queries import SCORE_NAMES
from session_doctor.store.trend_models import (
    DailyCalendarCell,
    RecurrenceEvidence,
    TrendBucket,
    TrendCohort,
    TrendJudgment,
    TrendMetrics,
    TrendReport,
)

from .components import (
    attr,
    badge,
    card,
    code,
    definition_list,
    disclosure,
    display_value,
    empty_state,
    humanize,
    table,
    text,
)
from .document import HtmlRenderError, document

CHART_WIDTH = 960
PLOT_LEFT = 56
PLOT_TOP = 18
PLOT_WIDTH = 884
PLOT_HEIGHT = 220
PLOT_BASE = PLOT_TOP + PLOT_HEIGHT
CHART_HEIGHT = PLOT_BASE + 34
SEGMENT_GAP = 2.0
MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def render_trends_html(report: TrendReport) -> str:
    try:
        body = (
            trends_header(report)
            + '<main id="main-content">'
            + coverage_section(report)
            + calendars_section(report)
            + charts_section(report)
            + judgments_section(report)
            + recurring_patterns_section(report)
            + "</main>"
            + '<footer class="section muted"><p>Generated locally by session-doctor. '
            "Observed dates use stored timezone-naive datetimes; no timezone conversion or "
            "causal interpretation is applied.</p></footer>"
        )
        return document("Session trends dashboard", body)
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise HtmlRenderError("HTML trends dashboard could not be rendered") from exc


def trends_header(report: TrendReport) -> str:
    filters = report.filters
    return (
        '<header role="banner">'
        '<p class="lede">Project-level diagnostic history</p>'
        "<h1>Session trends dashboard</h1>"
        '<div class="status-row">'
        f"{badge('Top-level and sidechain cohorts separated', 'neutral')}"
        f"{badge(humanize(filters.bucket.value) + ' buckets', 'neutral')}"
        "</div>"
        '<div class="grid section">'
        + card(
            definition_list(
                [
                    (
                        "Project filter",
                        redact_home(filters.project_path) if filters.project_path else None,
                        True,
                    ),
                    ("Agent filter", filters.agent_name, True),
                    ("Bucket", filters.bucket.value, True),
                    ("Periods", filters.periods, False),
                    ("Detail limit", filters.limit, False),
                ]
            ),
            heading="Selected filters",
        )
        + card(
            definition_list(
                [
                    ("Window start", report.window.start, False),
                    ("Window end", report.window.end, False),
                    ("Window anchor", report.window.anchor, True),
                    ("Latest matching session", report.window.latest_session_at, False),
                ]
            ),
            heading="Observed window",
        )
        + "</div></header>"
    )


def coverage_section(report: TrendReport) -> str:
    scope = report.scope
    matching = scope.matching_analysis
    windowed = scope.windowed_analysis
    return (
        '<section class="section" aria-labelledby="scope-coverage">'
        '<h2 id="scope-coverage">Scope and analysis coverage</h2>'
        '<div class="grid kpi">'
        + "".join(
            stat_card(label, value)
            for label, value in (
                ("Matching sessions", scope.matching_sessions),
                ("Windowed sessions", scope.windowed_sessions),
                ("Outside window", scope.outside_window_sessions),
                ("Untimed sessions", scope.untimed_sessions),
            )
        )
        + '</div><div class="grid section">'
        + card(
            analysis_counts(matching),
            heading="Matching-session analysis compatibility",
        )
        + card(
            analysis_counts(windowed),
            heading="Windowed-session analysis compatibility",
        )
        + "</div></section>"
    )


def stat_card(label: str, value: int) -> str:
    return card(f'<div class="stat">{value}</div><div class="stat-label">{text(label)}</div>')


def analysis_counts(counts) -> str:
    return definition_list(
        [
            ("Current", counts.current, False),
            ("Stale", counts.stale, False),
            ("Never analyzed", counts.never, False),
            (
                "Analyzer versions",
                ", ".join(
                    f"{row.analyzer_version}: {row.session_count}" for row in counts.version_counts
                )
                or None,
                True,
            ),
        ]
    )


def calendars_section(report: TrendReport) -> str:
    controls = (
        '<div class="controls" data-calendar-controls hidden aria-label="Calendar metric">'
        '<button type="button" data-calendar-metric="volume" aria-pressed="true">'
        "Session volume</button>"
        '<button type="button" data-calendar-metric="risk" aria-pressed="false">'
        "Risky-session rate</button></div>"
    )
    return (
        '<section class="section" aria-labelledby="contribution-calendars">'
        '<h2 id="contribution-calendars">Contribution calendars</h2>'
        '<p class="muted">Cells are complete observed dates in the selected window. '
        "Volume is neutral. Risk uses current analyzed sessions as its denominator; "
        "unavailable rates remain unavailable.</p>"
        f"{controls}"
        + calendar_views("Top-level", report.cohorts.top_level)
        + calendar_views("Sidechain", report.cohorts.sidechain)
        + "</section>"
    )


def calendar_views(name: str, cohort: TrendCohort) -> str:
    if not cohort.calendar:
        return card(
            empty_state("No observed-date window is available for this cohort."),
            heading=f"{name} calendar",
        )
    return (
        f'<article class="card"><h3>{text(name)} calendar</h3>'
        '<div data-calendar-view="volume">'
        f"{calendar_grid(cohort.calendar, 'volume', name)}"
        "</div>"
        '<div data-calendar-view="risk">'
        f"{calendar_grid(cohort.calendar, 'risk', name)}"
        "</div>"
        f"{calendar_summary(cohort.calendar)}"
        "</article>"
    )


def calendar_grid(
    cells: tuple[DailyCalendarCell, ...],
    metric: str,
    cohort_name: str,
) -> str:
    max_sessions = max((cell.sessions for cell in cells), default=0)
    offset = cells[0].observed_date.weekday()
    placeholders = "".join(
        '<li class="calendar-cell calendar-placeholder" aria-hidden="true"></li>'
        for _ in range(offset)
    )
    items = []
    for cell in cells:
        label = calendar_cell_label(cell)
        css_class = calendar_cell_class(cell, metric, max_sessions)
        items.append(
            f'<li class="calendar-cell {css_class}" title="{attr(label)}">'
            f'<span class="sr-only">{text(label)}</span></li>'
        )
    return (
        '<div class="calendar-wrap"><div class="calendar">'
        f'<div class="calendar-months" aria-hidden="true">{calendar_months(cells, offset)}</div>'
        '<div class="calendar-body">'
        '<div class="calendar-weekdays" aria-hidden="true">'
        '<span style="grid-row: 1">Mon</span>'
        '<span style="grid-row: 3">Wed</span>'
        '<span style="grid-row: 5">Fri</span></div>'
        f'<ol class="calendar-grid" aria-label="{attr(cohort_name)} {attr(metric)} '
        f'calendar">{placeholders}{"".join(items)}</ol>'
        "</div></div></div>" + calendar_legend(metric)
    )


def calendar_months(cells: tuple[DailyCalendarCell, ...], offset: int) -> str:
    labels = []
    last_week = None
    for index, cell in enumerate(cells):
        if index and cell.observed_date.day != 1:
            continue
        week = (index + offset) // 7
        if last_week is not None and week - last_week < 3:
            continue
        month_name = MONTH_ABBREVIATIONS[cell.observed_date.month - 1]
        labels.append(f'<span style="left: {week}rem">{month_name}</span>')
        last_week = week
    return "".join(labels)


def calendar_legend(metric: str) -> str:
    if metric == "volume":
        swatches = "".join(
            f'<span class="legend-cell{" level-" + str(level) if level else ""}"></span>'
            for level in range(5)
        )
        return f'<p class="calendar-legend">Fewer {swatches} more sessions per day</p>'
    swatches = "".join(f'<span class="legend-cell risk-{level}"></span>' for level in range(1, 5))
    return (
        '<p class="calendar-legend">0% <span class="legend-cell"></span>'
        f'{swatches} 100% risky<span class="gap"></span>'
        '<span class="legend-cell unavailable"></span> rate unavailable</p>'
    )


def calendar_cell_label(cell: DailyCalendarCell) -> str:
    coverage = percent(cell.current_analysis_coverage)
    risk_rate = percent(cell.risky_session_rate)
    return (
        f"Observed date {cell.observed_date.isoformat()}; {cell.sessions} sessions; "
        f"analysis current {cell.current_analyzed}, stale {cell.stale_analysis}, "
        f"never {cell.never_analyzed}; current-analysis coverage {coverage}; "
        f"risky sessions {cell.risky_sessions} of {cell.current_analyzed} current analyzed; "
        f"risky-session rate {risk_rate}"
    )


def calendar_cell_class(cell: DailyCalendarCell, metric: str, max_sessions: int) -> str:
    if metric == "volume":
        if not cell.sessions or not max_sessions:
            return "level-0"
        level = max(1, math.ceil((cell.sessions / max_sessions) * 4))
        return f"level-{min(level, 4)}"
    rate = cell.risky_session_rate
    if rate is None:
        return "unavailable"
    if rate == 0:
        return "risk-0"
    return f"risk-{min(max(1, math.ceil(rate * 4)), 4)}"


def calendar_summary(cells: tuple[DailyCalendarCell, ...]) -> str:
    sessions = sum(cell.sessions for cell in cells)
    current = sum(cell.current_analyzed for cell in cells)
    risky = sum(cell.risky_sessions for cell in cells)
    return (
        '<p class="muted">Calendar totals: '
        f"{sessions} sessions; {current} current analyzed; {risky} risky. "
        f"Current-analysis coverage {percent(current / sessions if sessions else None)}; "
        f"risky-session rate {percent(risky / current if current else None)}.</p>"
    )


def charts_section(report: TrendReport) -> str:
    return (
        '<section class="section" aria-labelledby="trend-charts">'
        '<h2 id="trend-charts">Trend charts</h2>'
        '<p class="muted">Every chart uses the exact half-open bucket boundaries from the '
        "typed trend report and retains a tabular text alternative.</p>"
        + cohort_charts("top-level", "Top-level", report.cohorts.top_level)
        + cohort_charts("sidechain", "Sidechain", report.cohorts.sidechain)
        + "</section>"
    )


def cohort_charts(identifier: str, name: str, cohort: TrendCohort) -> str:
    if not cohort.buckets:
        return card(
            empty_state("No nonempty cohort buckets are available for trend charts."),
            heading=f"{name} trends",
        )
    return (
        f"<h3>{text(name)} trends</h3>"
        + volume_coverage_chart(identifier, cohort.buckets)
        + score_chart(identifier, cohort.buckets)
        + risk_chart(identifier, cohort.buckets)
    )


def volume_coverage_chart(identifier: str, buckets: tuple[TrendBucket, ...]) -> str:
    max_total = max((bucket.metrics.sessions for bucket in buckets), default=1) or 1
    step = PLOT_WIDTH / len(buckets)
    elements = chart_shell(
        f"{identifier}-volume-title",
        f"{identifier}-volume-description",
        "Session volume and current-analysis coverage",
        "Stacked bars show sessions per bucket split into current-analyzed sessions and "
        "sessions without current analysis.",
    )
    elements.append(chart_frame(count_ticks(max_total), x_ticks(buckets, step)))
    for index, bucket in enumerate(buckets):
        total = bucket.metrics.sessions
        if not total:
            continue
        analyzed = min(max(bucket.metrics.current_analyzed, 0), total)
        rest = total - analyzed
        bar_width = min(step * 0.62, 24.0)
        x = PLOT_LEFT + (index + 0.5) * step - bar_width / 2
        analyzed_height = (analyzed / max_total) * PLOT_HEIGHT
        rest_height = (rest / max_total) * PLOT_HEIGHT
        title = (
            f"{bucket_label(bucket)}: {total} sessions; {analyzed} current analyzed; "
            f"coverage {percent(bucket.metrics.current_analysis_coverage)}"
        )
        if analyzed:
            elements.append(
                bar_mark(
                    x,
                    PLOT_BASE - analyzed_height,
                    bar_width,
                    analyzed_height,
                    "volume",
                    title,
                    rounded_top=not rest,
                )
            )
        if rest:
            gap = SEGMENT_GAP if analyzed else 0.0
            rest_top = PLOT_BASE - analyzed_height - rest_height
            elements.append(
                bar_mark(x, rest_top, bar_width, rest_height - gap, "volume-rest", title)
            )
    elements.append("</svg></div>")
    legend = chart_legend([("series-1", "Current analyzed"), ("rest", "Without current analysis")])
    rows = [
        bucket_row(bucket)
        + [
            text(bucket.metrics.sessions),
            text(bucket.metrics.current_analyzed),
            text(percent(bucket.metrics.current_analysis_coverage)),
        ]
        for bucket in buckets
    ]
    return card(
        "".join(elements)
        + legend
        + disclosure(
            "Data table: session volume and analysis coverage",
            table(
                ["Bucket start", "Bucket end", "Sessions", "Current analyzed", "Coverage"],
                rows,
                caption="Text alternative: session volume and analysis coverage",
            ),
        ),
        heading="Session volume and analysis coverage",
    )


def score_chart(identifier: str, buckets: tuple[TrendBucket, ...]) -> str:
    step = PLOT_WIDTH / len(buckets)
    elements = chart_shell(
        f"{identifier}-scores-title",
        f"{identifier}-scores-description",
        "Score averages",
        "Lines connect available 0 to 1 score averages by bucket. Gaps mean no samples.",
    )
    elements.append(chart_frame([(0.0, "0.0"), (0.5, "0.5"), (1.0, "1.0")], x_ticks(buckets, step)))
    for score_index, score_name in enumerate(SCORE_NAMES):
        points: list[tuple[int, float, int]] = []
        for bucket_index, bucket in enumerate(buckets):
            score = next(row for row in bucket.metrics.scores if row.metric_name == score_name)
            if score.average is None:
                continue
            points.append((bucket_index, score.average, score.sample_count))
        for run in consecutive_runs(points):
            if len(run) > 1:
                coordinates = " ".join(
                    f"{PLOT_LEFT + (bucket_index + 0.5) * step:.2f},"
                    f"{PLOT_TOP + (1 - value) * PLOT_HEIGHT:.2f}"
                    for bucket_index, value, _ in run
                )
                elements.append(
                    f'<polyline class="series-line series-{score_index + 1}" aria-hidden="true" '
                    f'points="{coordinates}"></polyline>'
                )
        for bucket_index, value, sample_count in points:
            cx = PLOT_LEFT + (bucket_index + 0.5) * step
            cy = PLOT_TOP + (1 - value) * PLOT_HEIGHT
            elements.append(
                f'<circle class="series-dot series-{score_index + 1}" aria-hidden="true" '
                f'cx="{cx:.2f}" cy="{cy:.2f}" r="4">'
                f"<title>{text(humanize(score_name))} {bucket_label(buckets[bucket_index])}: "
                f"{number(value)} ({sample_count} sample{'s' if sample_count != 1 else ''})"
                "</title></circle>"
            )
    elements.append("</svg></div>")
    rows = []
    for bucket in buckets:
        row = bucket_row(bucket)
        for score_name in SCORE_NAMES:
            score = next(item for item in bucket.metrics.scores if item.metric_name == score_name)
            row.append(
                f"{number(score.average)} ({score.sample_count} sample"
                f"{'s' if score.sample_count != 1 else ''})"
            )
        rows.append(row)
    legend = chart_legend(
        [(f"series-{index + 1}", humanize(name)) for index, name in enumerate(SCORE_NAMES)]
    )
    return card(
        "".join(elements)
        + legend
        + disclosure(
            "Data table: score averages and sample counts",
            table(
                ["Bucket start", "Bucket end", *[humanize(name) for name in SCORE_NAMES]],
                rows,
                caption="Text alternative: score averages and sample counts",
            ),
        ),
        heading="Score averages",
    )


def risk_chart(identifier: str, buckets: tuple[TrendBucket, ...]) -> str:
    step = PLOT_WIDTH / len(buckets)
    elements = chart_shell(
        f"{identifier}-risk-title",
        f"{identifier}-risk-description",
        "Risky-session rate",
        "Bars show risky-session rate among current analyzed sessions. Missing bars mean the "
        "rate is unavailable.",
    )
    elements.append(chart_frame([(0.0, "0%"), (0.5, "50%"), (1.0, "100%")], x_ticks(buckets, step)))
    for index, bucket in enumerate(buckets):
        rate = bucket.metrics.risky_session_rate
        if rate is None:
            continue
        bar_width = min(step * 0.5, 20.0)
        x = PLOT_LEFT + (index + 0.5) * step - bar_width / 2
        bar_height = rate * PLOT_HEIGHT
        title = (
            f"{bucket_label(bucket)}: {bucket.metrics.risky_sessions} of "
            f"{bucket.metrics.current_analyzed} current analyzed risky ({percent(rate)})"
        )
        elements.append(
            bar_mark(x, PLOT_BASE - bar_height, bar_width, bar_height, "risk-bar", title)
        )
    elements.append("</svg></div>")
    rows = [
        bucket_row(bucket)
        + [
            text(bucket.metrics.risky_sessions),
            text(bucket.metrics.current_analyzed),
            text(percent(bucket.metrics.risky_session_rate)),
        ]
        for bucket in buckets
    ]
    return card(
        "".join(elements)
        + disclosure(
            "Data table: risky-session rates and denominators",
            table(
                [
                    "Bucket start",
                    "Bucket end",
                    "Risky sessions",
                    "Current analyzed denominator",
                    "Rate",
                ],
                rows,
                caption="Text alternative: risky-session rates and denominators",
            ),
        ),
        heading="Risky-session rate",
    )


def chart_shell(
    title_id: str,
    description_id: str,
    title: str,
    description: str,
) -> list[str]:
    return [
        '<div class="chart-scroll">'
        f'<svg class="trend-chart" role="img" aria-labelledby="{attr(title_id)} '
        f'{attr(description_id)}" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}">',
        f'<title id="{attr(title_id)}">{text(title)}</title>',
        f'<desc id="{attr(description_id)}">{text(description)}</desc>',
    ]


def chart_frame(
    y_ticks: Iterable[tuple[float, str]],
    x_tick_positions: Iterable[tuple[float, str]],
) -> str:
    parts = []
    for fraction, label in y_ticks:
        y = PLOT_TOP + (1 - fraction) * PLOT_HEIGHT
        if fraction > 0:
            parts.append(
                f'<line class="grid-h" aria-hidden="true" x1="{PLOT_LEFT}" y1="{y:.2f}" '
                f'x2="{PLOT_LEFT + PLOT_WIDTH}" y2="{y:.2f}"></line>'
            )
        parts.append(
            f'<text class="tick-label" aria-hidden="true" text-anchor="end" '
            f'x="{PLOT_LEFT - 8}" y="{y + 3.5:.2f}">{text(label)}</text>'
        )
    parts.append(
        f'<line class="axis" aria-hidden="true" x1="{PLOT_LEFT}" y1="{PLOT_BASE}" '
        f'x2="{PLOT_LEFT + PLOT_WIDTH}" y2="{PLOT_BASE}"></line>'
    )
    for x, label in x_tick_positions:
        parts.append(
            f'<line class="axis" aria-hidden="true" x1="{x:.2f}" y1="{PLOT_BASE}" '
            f'x2="{x:.2f}" y2="{PLOT_BASE + 4}"></line>'
        )
        parts.append(
            f'<text class="tick-label" aria-hidden="true" text-anchor="middle" '
            f'x="{x:.2f}" y="{PLOT_BASE + 18}">{text(label)}</text>'
        )
    return "".join(parts)


def count_ticks(max_value: int) -> list[tuple[float, str]]:
    values = sorted({0, round(max_value / 2), max_value})
    return [(value / max_value, str(value)) for value in values]


def x_ticks(buckets: tuple[TrendBucket, ...], step: float) -> list[tuple[float, str]]:
    stride = max(1, math.ceil(len(buckets) / 7))
    indices = list(range(0, len(buckets), stride))
    last = len(buckets) - 1
    if indices[-1] != last:
        if last - indices[-1] < stride:
            indices[-1] = last
        else:
            indices.append(last)
    return [(PLOT_LEFT + (index + 0.5) * step, bucket_label(buckets[index])) for index in indices]


def bucket_label(bucket: TrendBucket) -> str:
    return bucket.start.date().isoformat() if bucket.start is not None else "n/a"


def bar_mark(
    x: float,
    y_top: float,
    width: float,
    height: float,
    css_class: str,
    title: str,
    *,
    rounded_top: bool = True,
) -> str:
    height = max(height, 1.5)
    radius = min(3.0, width / 2, height) if rounded_top else 0.0
    if radius > 0.5:
        shape = (
            f'<path class="{css_class}" aria-hidden="true" d="M{x:.2f} {y_top + height:.2f} '
            f"v{-(height - radius):.2f} q0 {-radius:.2f} {radius:.2f} {-radius:.2f} "
            f"h{width - 2 * radius:.2f} q{radius:.2f} 0 {radius:.2f} {radius:.2f} "
            f'v{height - radius:.2f} z">'
        )
        return shape + f"<title>{text(title)}</title></path>"
    return (
        f'<rect class="{css_class}" aria-hidden="true" x="{x:.2f}" y="{y_top:.2f}" '
        f'width="{width:.2f}" height="{height:.2f}">'
        f"<title>{text(title)}</title></rect>"
    )


def consecutive_runs(
    points: list[tuple[int, float, int]],
) -> list[list[tuple[int, float, int]]]:
    runs: list[list[tuple[int, float, int]]] = []
    for point in points:
        if runs and point[0] == runs[-1][-1][0] + 1:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


def chart_legend(entries: list[tuple[str, str]]) -> str:
    return (
        '<ul class="legend">'
        + "".join(
            f'<li><span class="legend-key {key}"></span>{text(label)}</li>'
            for key, label in entries
        )
        + "</ul>"
    )


def bucket_row(bucket: TrendBucket) -> list[str]:
    return [display_value(bucket.start), display_value(bucket.end)]


def judgments_section(report: TrendReport) -> str:
    return (
        '<section class="section" aria-labelledby="judgments-distributions">'
        '<h2 id="judgments-distributions">Judgments and distributions</h2>'
        '<p class="muted">Judgments retain their guarded insufficiency reasons and comparison '
        "inputs. Agent observations are descriptive and are not rankings.</p>"
        + cohort_judgments("Top-level", report.cohorts.top_level)
        + cohort_judgments("Sidechain", report.cohorts.sidechain)
        + "</section>"
    )


def cohort_judgments(name: str, cohort: TrendCohort) -> str:
    judgments = table(
        [
            "Metric",
            "Status",
            "Earlier",
            "Recent",
            "Delta",
            "Earlier samples",
            "Recent samples",
            "Earlier nonempty buckets",
            "Recent nonempty buckets",
            "Earlier current coverage",
            "Recent current coverage",
            "Earlier sample coverage",
            "Recent sample coverage",
            "Threshold",
            "Comparison method",
            "Reasons",
        ],
        [judgment_row(row) for row in cohort.judgments],
        caption=f"{name} guarded judgments",
    )
    classifications = (
        table(
            ["Classification", "Sessions", "Rate among current analyzed"],
            [
                [code(row.label), text(row.session_count), text(percent(row.rate))]
                for row in cohort.totals.classifications
            ],
            caption=f"{name} classification distribution",
        )
        if cohort.totals.classifications
        else empty_state("No current classification distribution is available.")
    )
    agents = (
        table(
            [
                "Agent",
                "Sessions",
                "Current analyzed denominator",
                "Current coverage",
                "Risky-session rate",
                *[humanize(name) for name in SCORE_NAMES],
            ],
            [
                [
                    code(row.agent_name),
                    text(row.metrics.sessions),
                    text(row.metrics.current_analyzed),
                    text(percent(row.metrics.current_analysis_coverage)),
                    text(percent(row.metrics.risky_session_rate)),
                    *[score_observation(row.metrics, score_name) for score_name in SCORE_NAMES],
                ]
                for row in cohort.agents
            ],
            caption=f"{name} agent observations (not ranked)",
        )
        if cohort.agents
        else empty_state("No agent observations are available for this cohort.")
    )
    totals = cohort.totals
    summary = definition_list(
        [
            ("Sessions", totals.sessions, False),
            ("Current analyzed", totals.current_analyzed, False),
            ("Stale analysis", totals.stale_analysis, False),
            ("Never analyzed", totals.never_analyzed, False),
            ("Current-analysis coverage", percent(totals.current_analysis_coverage), False),
            ("Risky sessions", totals.risky_sessions, False),
            ("Risky-session rate", percent(totals.risky_session_rate), False),
        ]
    )
    return (
        f"<h3>{text(name)} cohort</h3>"
        '<div class="grid">'
        + card(summary, heading="Cohort totals")
        + card(classifications, heading="Classifications")
        + card(agents, heading="Agent observations")
        + "</div>"
        + disclosure(f"{name} guarded judgments", judgments)
    )


def judgment_row(row: TrendJudgment) -> list[str]:
    return [
        code(row.metric_name),
        code(row.status.value),
        text(number(row.earlier_value)),
        text(number(row.recent_value)),
        text(number(row.delta)),
        text(row.earlier_sample_count),
        text(row.recent_sample_count),
        text(row.earlier_nonempty_buckets),
        text(row.recent_nonempty_buckets),
        text(percent(row.earlier_current_analysis_coverage)),
        text(percent(row.recent_current_analysis_coverage)),
        text(percent(row.earlier_sample_coverage)),
        text(percent(row.recent_sample_coverage)),
        text(number(row.threshold)),
        code(row.comparison_method),
        ", ".join(code(reason) for reason in row.reasons) or "None",
    ]


def score_observation(metrics: TrendMetrics, score_name: str) -> str:
    score = next(row for row in metrics.scores if row.metric_name == score_name)
    return f"{number(score.average)} ({score.sample_count} samples)"


def recurring_patterns_section(report: TrendReport) -> str:
    patterns = report.recurring_patterns
    exclusions = patterns.family_exclusions
    return (
        '<section class="section" aria-labelledby="recurring-patterns">'
        '<h2 id="recurring-patterns">Recurring patterns</h2>'
        + card(
            definition_list(
                [
                    ("Orphan-parent exclusions", exclusions.orphan_parent, False),
                    ("Cycle exclusions", exclusions.cycle, False),
                    ("Cross-agent-parent exclusions", exclusions.cross_agent_parent, False),
                ]
            ),
            heading="Root-family exclusions",
        )
        + pattern_disclosure(
            "Recurring failed commands",
            report.recurring_patterns.failed_commands,
            lambda row: code(row.command),
        )
        + pattern_disclosure(
            "Recurring failed tool results",
            report.recurring_patterns.failed_tool_results,
            lambda row: code(row.tool_name) + " · " + code(row.fingerprint_id),
        )
        + pattern_disclosure(
            "Recurring problematic files",
            report.recurring_patterns.problematic_files,
            lambda row: code(redact_home(row.path)),
        )
        + "</section>"
    )


def pattern_disclosure(title: str, patterns, label: Callable[[Any], str]) -> str:
    if not patterns:
        return card(empty_state("No qualifying recurring patterns."), heading=title)
    rows = [[label(row), *recurrence_cells(row.evidence)] for row in patterns]
    return disclosure(
        f"{title} — {len(rows)} displayed",
        table(
            [
                "Pattern",
                "Events",
                "Sessions",
                "Root families",
                "Top-level",
                "Sidechain",
                "Agents",
                "Active buckets",
                "First observed",
                "Most recent",
                "Example session",
            ],
            rows,
            caption=title,
        ),
    )


def recurrence_cells(evidence: RecurrenceEvidence) -> list[str]:
    return [
        text(evidence.event_count),
        text(evidence.session_count),
        text(evidence.root_family_count),
        text(evidence.top_level_session_count),
        text(evidence.sidechain_session_count),
        text(", ".join(evidence.agents) or "None"),
        text(evidence.active_bucket_count),
        display_value(evidence.first_at),
        display_value(evidence.most_recent_at),
        code(evidence.example_session_id),
    ]


def percent(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value * 100:.1f}%"


def number(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value:.3f}"

from __future__ import annotations

import math
from collections.abc import Callable
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
        '<div class="grid">'
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
        '<div data-calendar-view="risk" hidden>'
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
    placeholders = "".join(
        '<li class="calendar-cell calendar-placeholder" aria-hidden="true"></li>'
        for _ in range(cells[0].observed_date.weekday())
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
        '<div class="calendar-wrap">'
        f'<ol class="calendar-grid" aria-label="{attr(cohort_name)} {attr(metric)} '
        f'calendar">{placeholders}{"".join(items)}</ol></div>'
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
    width, height, left, top = 960, 260, 70, 25
    plot_width, plot_height = 870, 190
    max_sessions = max((bucket.metrics.sessions for bucket in buckets), default=1) or 1
    step = plot_width / len(buckets)
    elements = chart_shell(
        f"{identifier}-volume-title",
        f"{identifier}-volume-description",
        "Session volume and current-analysis coverage",
        "Neutral bars show session counts. Green points show current-analysis coverage; "
        "missing points mean coverage is unavailable.",
        width,
        height,
    )
    elements.append(axis_markup(left, top, plot_width, plot_height))
    for index, bucket in enumerate(buckets):
        x = left + index * step + step * 0.15
        bar_height = (bucket.metrics.sessions / max_sessions) * plot_height
        elements.append(
            f'<rect class="volume" aria-hidden="true" x="{x:.2f}" '
            f'y="{top + plot_height - bar_height:.2f}" width="{step * 0.7:.2f}" '
            f'height="{bar_height:.2f}"></rect>'
        )
        coverage = bucket.metrics.current_analysis_coverage
        if coverage is not None:
            cy = top + (1 - coverage) * plot_height
            cx = left + (index + 0.5) * step
            elements.append(
                f'<circle class="coverage" aria-hidden="true" cx="{cx:.2f}" '
                f'cy="{cy:.2f}" r="4"></circle>'
            )
    elements.append("</svg></div>")
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
        + table(
            ["Bucket start", "Bucket end", "Sessions", "Current analyzed", "Coverage"],
            rows,
            caption="Text alternative: session volume and analysis coverage",
        ),
        heading="Session volume and analysis coverage",
    )


def score_chart(identifier: str, buckets: tuple[TrendBucket, ...]) -> str:
    width, height, left, top = 960, 260, 70, 25
    plot_width, plot_height = 870, 190
    step = plot_width / len(buckets)
    elements = chart_shell(
        f"{identifier}-scores-title",
        f"{identifier}-scores-description",
        "Score averages",
        "Points show available 0 to 1 score averages by bucket. Missing points mean no samples.",
        width,
        height,
    )
    elements.append(axis_markup(left, top, plot_width, plot_height))
    for score_index, score_name in enumerate(SCORE_NAMES):
        for bucket_index, bucket in enumerate(buckets):
            score = next(row for row in bucket.metrics.scores if row.metric_name == score_name)
            if score.average is None:
                continue
            cx = left + (bucket_index + 0.5) * step
            cy = top + (1 - score.average) * plot_height
            elements.append(
                f'<circle class="series-{score_index}" aria-hidden="true" cx="{cx:.2f}" '
                f'cy="{cy:.2f}" r="4"></circle>'
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
    legend = (
        '<ul class="legend">'
        + "".join(
            f'<li><span class="legend-key series-{index}"></span>{text(humanize(name))}</li>'
            for index, name in enumerate(SCORE_NAMES)
        )
        + "</ul>"
    )
    return card(
        "".join(elements)
        + legend
        + table(
            ["Bucket start", "Bucket end", *[humanize(name) for name in SCORE_NAMES]],
            rows,
            caption="Text alternative: score averages and sample counts",
        ),
        heading="Score averages",
    )


def risk_chart(identifier: str, buckets: tuple[TrendBucket, ...]) -> str:
    width, height, left, top = 960, 260, 70, 25
    plot_width, plot_height = 870, 190
    step = plot_width / len(buckets)
    elements = chart_shell(
        f"{identifier}-risk-title",
        f"{identifier}-risk-description",
        "Risky-session rate",
        "Bars show risky-session rate among current analyzed sessions. Missing bars mean the "
        "rate is unavailable.",
        width,
        height,
    )
    elements.append(axis_markup(left, top, plot_width, plot_height))
    for index, bucket in enumerate(buckets):
        rate = bucket.metrics.risky_session_rate
        if rate is None:
            continue
        bar_height = rate * plot_height
        elements.append(
            f'<rect class="risk-bar" aria-hidden="true" '
            f'x="{left + index * step + step * 0.15:.2f}" '
            f'y="{top + plot_height - bar_height:.2f}" width="{step * 0.7:.2f}" '
            f'height="{bar_height:.2f}"></rect>'
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
        + table(
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
        heading="Risky-session rate",
    )


def chart_shell(
    title_id: str,
    description_id: str,
    title: str,
    description: str,
    width: int,
    height: int,
) -> list[str]:
    return [
        '<div class="chart-scroll">'
        f'<svg class="trend-chart" role="img" aria-labelledby="{attr(title_id)} '
        f'{attr(description_id)}" viewBox="0 0 {width} {height}">',
        f'<title id="{attr(title_id)}">{text(title)}</title>',
        f'<desc id="{attr(description_id)}">{text(description)}</desc>',
    ]


def axis_markup(left: int, top: int, width: int, height: int) -> str:
    return (
        f'<path class="axis" aria-hidden="true" d="M {left} {top} V {top + height} '
        f'H {left + width}"></path>'
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
                "Current coverage",
                "Risky-session rate",
                *[humanize(name) for name in SCORE_NAMES],
            ],
            [
                [
                    code(row.agent_name),
                    text(row.metrics.sessions),
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

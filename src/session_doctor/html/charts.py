from __future__ import annotations

from session_doctor.report_models import SessionSequence

from .components import disclosure, display_value, table, text

LANES = (
    ("user_message", "User messages", False),
    ("assistant_message", "Assistant messages", False),
    ("tool_call", "Tool calls", False),
    ("tool_result", "Tool results", False),
    ("tool_failure", "Tool failures", True),
    ("command_success", "Commands succeeded", False),
    ("command_failure", "Commands failed", True),
    ("command_unknown", "Command status unknown", False),
    ("file_activity", "File activity", False),
    ("parse_warning", "Parse warnings", True),
)
RISK_MARKER_CATEGORIES = {
    "repeated_requests",
    "corrections",
    "ambiguity_markers",
    "scope_boundaries",
    "command_failures",
    "tool_failures",
    "repeated_failures",
    "repeated_file_edits",
    "frustration_markers",
    "stop_or_pause_markers",
}

WIDTH = 960
LABEL_WIDTH = 170
PLOT_RIGHT = WIDTH - 16
LANE_HEIGHT = 26
TOP = 40


def sequence_chart(sequence: SessionSequence) -> str:
    fallback = sequence_fallback(sequence)
    if not sequence.bins:
        return (
            '<div class="empty">No resolved source record range is available. '
            f"{text(sequence.total_unresolved_activities)} activities remain unresolved.</div>"
            + fallback
        )
    plot_width = PLOT_RIGHT - LABEL_WIDTH
    baseline = TOP + len(LANES) * LANE_HEIGHT
    height = baseline + 26
    bin_width = plot_width / len(sequence.bins)
    max_count = max(
        (getattr(row.counts, category) for row in sequence.bins for category, _, _ in LANES),
        default=0,
    )
    max_count = max(max_count, 1)
    elements = [
        '<svg class="sequence-chart" role="img" '
        'aria-labelledby="sequence-title sequence-description" '
        f'viewBox="0 0 {WIDTH} {height}">',
        '<title id="sequence-title">Session activity by source record order</title>',
        '<desc id="sequence-description">Activity density by normalized category. '
        "Horizontal position represents source record order, not elapsed time or causality. "
        f"There are {text(sequence.total_resolved_activities)} resolved activities and "
        f"{text(sequence.total_unresolved_activities)} unresolved activities.</desc>",
    ]
    for lane_index, (category, label, risk) in enumerate(LANES):
        y = TOP + lane_index * LANE_HEIGHT
        if lane_index % 2:
            elements.append(
                f'<rect class="lane-band" aria-hidden="true" x="{LABEL_WIDTH}" y="{y}" '
                f'width="{plot_width}" height="{LANE_HEIGHT}"></rect>'
            )
        elements.append(
            f'<text class="lane-label" text-anchor="end" x="{LABEL_WIDTH - 10}" '
            f'y="{y + 17}">{text(label)}</text>'
        )
        for bin_index, bin_row in enumerate(sequence.bins):
            count = getattr(bin_row.counts, category)
            if not count:
                continue
            bar_height = max(3.0, (count / max_count) * (LANE_HEIGHT - 6))
            x = LABEL_WIDTH + bin_index * bin_width
            css_class = "activity-risk" if risk else "activity"
            elements.append(
                f'<rect class="{css_class}" aria-hidden="true" x="{x:.2f}" '
                f'y="{y + LANE_HEIGHT - bar_height - 1:.2f}" rx="1.5" '
                f'width="{max(bin_width - 2, 1):.2f}" height="{bar_height:.2f}">'
                f"<title>{text(label)}: {count}; records "
                f"{bin_row.first_record_index}–{bin_row.last_record_index}</title></rect>"
            )
    elements.append(
        f'<line class="axis" aria-hidden="true" x1="{LABEL_WIDTH}" y1="{baseline}" '
        f'x2="{PLOT_RIGHT}" y2="{baseline}"></line>'
    )
    first = sequence.first_record_index
    last = sequence.last_record_index
    assert first is not None and last is not None
    span = max(last - first, 1)
    for marker in sequence.evidence_markers:
        x = LABEL_WIDTH + ((marker.record_index - first) / span) * plot_width
        marker_class = (
            "marker marker-risk"
            if marker.category in RISK_MARKER_CATEGORIES
            else "marker marker-neutral"
        )
        elements.append(
            f'<line class="{marker_class}" aria-hidden="true" x1="{x:.2f}" '
            f'y1="{TOP - 6}" x2="{x:.2f}" y2="{baseline}">'
            f"<title>{text(marker.category)} evidence {text(marker.evidence_id)} at "
            f"record {marker.record_index}</title></line>"
        )
        elements.append(
            f'<circle class="{marker_class}" aria-hidden="true" cx="{x:.2f}" '
            f'cy="{TOP - 10}" r="3.5">'
            f"<title>{text(marker.category)} evidence marker</title></circle>"
        )
    elements.extend(
        [
            f'<text class="tick-label" x="{LABEL_WIDTH}" y="{height - 7}">Record {first}</text>',
            f'<text class="tick-label" text-anchor="end" x="{PLOT_RIGHT}" '
            f'y="{height - 7}">Record {last}</text>',
            "</svg>",
        ]
    )
    legend = (
        '<ul class="legend" aria-label="Sequence legend">'
        '<li><span class="legend-key"></span>Neutral activity density</li>'
        '<li><span class="legend-key risk"></span>Failure, warning, or negative evidence</li>'
        '<li><span class="legend-key evidence"></span>Neutral evidence marker</li>'
        "</ul>"
    )
    return (
        '<div class="chart-scroll">'
        + "".join(elements)
        + "</div>"
        + legend
        + fallback
        + marker_fallback(sequence)
    )


def sequence_fallback(sequence: SessionSequence) -> str:
    resolved = sequence.resolved_activity_counts
    unresolved = sequence.unresolved_activity_counts
    rows = [
        [text(label), text(getattr(resolved, category)), text(getattr(unresolved, category))]
        for category, label, _ in LANES
    ]
    return disclosure(
        "Sequence activity totals",
        table(
            ["Activity", "Resolved", "Unresolved"],
            rows,
            caption="Text alternative: session sequence activity totals",
        ),
    )


def marker_fallback(sequence: SessionSequence) -> str:
    if not sequence.evidence_markers:
        return '<p class="muted">No resolved evidence markers are available.</p>'
    rows = [
        [
            text(marker.record_index),
            text(marker.category),
            text(marker.evidence_id),
            text(marker.source_event_id),
            display_value(marker.observed_at),
        ]
        for marker in sequence.evidence_markers
    ]
    return disclosure(
        f"Sequence evidence marker positions — {len(rows)} markers",
        table(
            ["Record", "Category", "Evidence ID", "Source event", "Observed at"],
            rows,
            caption="Text alternative: exact sequence evidence marker positions",
        ),
    )

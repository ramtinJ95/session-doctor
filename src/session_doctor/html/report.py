from __future__ import annotations

from datetime import timedelta

from session_doctor.analysis.classification_constants import NEGATIVE_LABELS
from session_doctor.report_models import (
    BoundedEvidence,
    BoundedPatterns,
    ClassificationReferenceEvidence,
    CommandFailureEvidence,
    FailedCommandPatternReport,
    FailedToolPatternReport,
    FailureGroupEvidence,
    FileLoopEvidence,
    MessageSignalEvidence,
    ProblematicFilePatternReport,
    ReportClassification,
    ReportEnding,
    ReportScore,
    ReportStatement,
    SessionReport,
    ToolFailureEvidence,
)

from .charts import sequence_chart
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
    statement_list,
    table,
    text,
)
from .document import HtmlRenderError, document


def render_report_html(report: SessionReport) -> str:
    try:
        body = (
            report_header(report)
            + '<main id="main-content">'
            + diagnostic_overview(report)
            + scores_section(report)
            + sequence_section(report)
            + evidence_section(report)
            + ending_recurrence_actions(report)
            + "</main>"
            + '<footer class="section muted"><p>Generated locally by session-doctor. '
            "Source record position does not imply elapsed time or causality.</p></footer>"
        )
        return document(f"Session report: {report.session.session_id}", body)
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise HtmlRenderError("HTML report could not be rendered") from exc


def report_header(report: SessionReport) -> str:
    session = report.session
    duration = session_duration(session.started_at, session.ended_at)
    analysis = report.analysis
    action = (
        f'<p class="notice"><strong>Recovery action:</strong> {code(analysis.action)}</p>'
        if analysis.action
        else ""
    )
    privacy_label = (
        "Displayed bounded evidence text included"
        if report.privacy.message_text_included
        else "Message text hidden"
    )
    privacy_badge = badge(
        privacy_label,
        "unavailable" if report.privacy.message_text_included else "neutral",
    )
    return (
        '<header role="banner">'
        '<p class="lede">Exact-session diagnostic report</p>'
        "<h1>Session report</h1>"
        f'<p class="session-key">Session {code(session.session_id)}</p>'
        '<div class="status-row">'
        f"{badge(humanize(analysis.status) + ' analysis', analysis.status)}"
        f"{badge('Sidechain' if session.is_sidechain else 'Top-level', 'neutral')}"
        f"{privacy_badge}"
        "</div>"
        '<div class="grid section">'
        + card(
            definition_list(
                [
                    ("Session ID", session.session_id, True),
                    ("Report schema", report.schema_version, False),
                    ("Agent", session.agent, True),
                    ("Model provider", session.model_provider, True),
                    ("Model", session.model, True),
                    ("Agent version", session.agent_version, True),
                    ("Parent session", session.parent_session_id, True),
                    ("Child sessions", ", ".join(session.child_session_ids) or None, True),
                ]
            ),
            heading="Identity",
        )
        + card(
            definition_list(
                [
                    ("Observed project hint", session.project_hint, True),
                    ("Project hint source", session.project_hint_source, True),
                    ("Started", session.started_at, False),
                    ("Ended", session.ended_at, False),
                    ("Duration", duration, False),
                ]
            ),
            heading="Observed scope and time",
        )
        + card(
            definition_list(
                [
                    ("Analysis status", analysis.status, True),
                    ("Current analyzer", analysis.current_analyzer_version, True),
                    ("Observed analyzer", analysis.observed_analyzer_version, True),
                    ("Analysis run", analysis.analysis_run_id, True),
                    ("Message text", privacy_label, False),
                    ("Disclosure scope", report.privacy.disclosure_scope, True),
                ]
            )
            + action,
            heading="Analysis and privacy",
        )
        + "</div></header>"
    )


def session_duration(started_at, ended_at) -> str | None:
    if started_at is None or ended_at is None:
        return None
    delta = ended_at - started_at
    if delta < timedelta(0):
        return None
    seconds = int(delta.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def diagnostic_overview(report: SessionReport) -> str:
    summary = report.summary
    stats = (
        ("Raw events", summary.raw_events),
        ("Messages", summary.messages),
        ("Tool calls", summary.tool_calls),
        ("Tool results", summary.tool_results),
        ("Commands", summary.command_runs),
        ("File activities", summary.file_activities),
        ("Parse warnings", summary.parse_warnings),
    )
    stat_cards = "".join(
        card(f'<div class="stat">{value}</div><div class="stat-label">{text(label)}</div>')
        for label, value in stats
    )
    classifications = (
        '<div class="grid">'
        + "".join(classification_card(row) for row in report.classifications)
        + "</div>"
        if report.classifications
        else empty_state(
            "No current classifications are available."
            if report.analysis.status != "current"
            else "No classifications were persisted for this analysis."
        )
    )
    warning_codes = (
        "<p><strong>Parse warning codes:</strong> "
        + ", ".join(code(item) for item in summary.parse_warning_codes)
        + "</p>"
        if summary.parse_warning_codes
        else ""
    )
    return (
        '<section class="section" aria-labelledby="diagnostic-overview">'
        '<h2 id="diagnostic-overview">Diagnostic overview</h2>'
        f'<div class="grid">{stat_cards}</div>{warning_codes}'
        "<h3>Primary classifications</h3>"
        f"{classifications}"
        "<h3>Ending-state availability</h3>"
        f"{ending_summary(report.ending)}"
        "</section>"
    )


def classification_card(row: ReportClassification) -> str:
    if row.label in {"healthy", "resolved_after_corrections"}:
        classification_status = "available"
    elif row.label in NEGATIVE_LABELS:
        classification_status = "risk"
    else:
        classification_status = "neutral"
    body = (
        '<div class="status-row">'
        f"{badge('Diagnostic classification', classification_status)}"
        f"<span><strong>{row.score:.3f}</strong> score · "
        f"<strong>{row.confidence:.3f}</strong> confidence</span></div>"
        f"<p>{text(row.evidence_summary)}</p>"
        f'<p class="muted">Evidence references: {len(row.source_event_ids)} resolved, '
        f"{len(row.unresolved_source_event_ids)} unresolved.</p>"
        f"<p>{code(row.classification_id)}</p>"
    )
    return card(body, heading=humanize(row.label))


def ending_summary(ending: ReportEnding) -> str:
    if ending.status == "unavailable":
        return (
            '<p class="notice">Ending-state analysis is unavailable. Reason: '
            f"{code(ending.reason)}</p>"
        )
    if ending.unresolved_ending_signal is None:
        statement = "No ending-state signal was available to evaluate."
        css = "notice"
    elif ending.unresolved_ending_signal:
        statement = "An unresolved ending signal was detected in persisted analysis."
        css = "notice risk"
    else:
        statement = "No unresolved ending signal was detected; this is not a resolution claim."
        css = "notice"
    return f'<p class="{css}">{text(statement)}</p>'


def scores_section(report: SessionReport) -> str:
    if not report.scores:
        content = empty_state(
            f"Scores are unavailable because analysis is {report.analysis.status}."
        )
    else:
        content = "".join(score_row(score) for score in report.scores)
    return (
        '<section class="section" aria-labelledby="scores-contributions">'
        '<h2 id="scores-contributions">Scores and contributions</h2>'
        '<p class="muted">Values use the existing 0–1 heuristic scale. They are not grades, '
        "percentiles, or measured probabilities.</p>"
        f"{content}</section>"
    )


def score_row(score: ReportScore) -> str:
    progress = (
        '<div class="score-row">'
        f"<strong>{text(humanize(score.name))}</strong>"
        f'<progress max="1" value="{score.value:.3f}" '
        f'aria-label="{attr(humanize(score.name))}: {score.value:.3f}"></progress>'
        f'<span class="score-value">{score.value:.3f}</span></div>'
    )
    names = sorted(
        set(score.component_values) | set(score.component_weights) | set(score.contributions)
    )
    rows = [
        [
            code(name),
            numeric(score.component_values.get(name)),
            numeric(score.component_weights.get(name)),
            numeric(score.contributions.get(name)),
        ]
        for name in names
    ]
    detail = (
        table(
            ["Component", "Value", "Weight", "Contribution"],
            rows,
            caption=f"{humanize(score.name)} calculation details",
        )
        + f'<p class="muted">Source events: {len(score.source_event_ids)} resolved, '
        f"{len(score.unresolved_source_event_ids)} unresolved.</p>"
    )
    return progress + disclosure(f"Explain {humanize(score.name)}", detail)


def numeric(value: float | None) -> str:
    return '<span class="muted">Unavailable</span>' if value is None else f"{value:.3f}"


def formatted_number(value: float | None) -> str | None:
    return None if value is None else f"{value:.3f}"


def sequence_section(report: SessionReport) -> str:
    sequence = report.sequence
    unresolved_markers = (
        table(
            ["Evidence category", "Unresolved markers"],
            [[code(row.category), text(row.count)] for row in sequence.unresolved_evidence_markers],
            caption="Unresolved evidence marker counts",
        )
        if sequence.unresolved_evidence_markers
        else ""
    )
    return (
        '<section class="section" aria-labelledby="session-sequence">'
        '<h2 id="session-sequence">Session sequence</h2>'
        '<p class="notice"><strong>Ordering basis:</strong> observed source record order. '
        "Horizontal position is not measured elapsed time and does not imply causality.</p>"
        f"{sequence_chart(sequence)}{unresolved_markers}</section>"
    )


def evidence_section(report: SessionReport) -> str:
    controls = (
        '<div class="controls" data-disclosure-controls hidden>'
        '<button type="button" data-disclosure-action="open">Expand all details</button>'
        '<button type="button" data-disclosure-action="close">Collapse all details</button>'
        "</div>"
    )
    sections = "".join(
        evidence_disclosure(name, bounded) for name, bounded in report.evidence.items()
    )
    return (
        '<section class="section" aria-labelledby="evidence">'
        '<h2 id="evidence">Evidence</h2>'
        '<p class="muted">Evidence sections retain their displayed, total, and omitted counts. '
        "Message text appears only when explicitly requested and only for displayed bounded rows."
        f"</p>{controls}{sections}</section>"
    )


def evidence_disclosure(name: str, section: BoundedEvidence) -> str:
    title = f"{humanize(name)} — {section.displayed}/{section.total} displayed" + (
        f", {section.omitted} omitted" if section.omitted else ""
    )
    if section.status == "unavailable":
        body = f'<p class="notice">Unavailable: {code(section.reason)}</p>'
        return card(body, heading=title)
    elif not section.items:
        body = empty_state("No persisted evidence in this section.")
        return card(body, heading=title)
    return disclosure(title, "".join(evidence_item(item) for item in section.items))


def evidence_item(item) -> str:
    common = [("Evidence ID", item.evidence_id, True), ("Type", item.item_type, True)]
    if isinstance(item, MessageSignalEvidence):
        rows = [
            *common,
            ("Feature", item.feature_name, True),
            ("Message", item.message_id, True),
            ("Role", item.role, True),
            ("Observed at", item.timestamp, False),
            ("Score", f"{item.score:.3f}", False),
            ("Matched message", item.matched_message_id, True),
            ("Source event", item.source_event_id, True),
            ("Matched source event", item.matched_source_event_id, True),
            ("Similarity", formatted_number(item.similarity_score), False),
        ]
        extra = (
            f"<p><strong>Displayed evidence text:</strong> {text(item.text)}</p>"
            if item.text is not None
            else ""
        )
    elif isinstance(item, CommandFailureEvidence):
        rows = [
            *common,
            ("Command run", item.command_run_id, True),
            ("Source event", item.source_event_id, True),
            ("Command", item.command_display, True),
            ("Exit code", item.exit_code, False),
            ("Fingerprint", item.fingerprint, True),
        ]
        extra = ""
    elif isinstance(item, ToolFailureEvidence):
        rows = [
            *common,
            ("Tool result", item.tool_result_id, True),
            ("Tool call", item.tool_call_id, True),
            ("Source event", item.source_event_id, True),
            ("Tool", item.tool_name, True),
            ("Output length", item.output_length, False),
            ("Fingerprint", item.fingerprint, True),
        ]
        extra = ""
    elif isinstance(item, FailureGroupEvidence):
        rows = [
            *common,
            ("Group type", item.group_type, True),
            ("Fingerprint", item.fingerprint, True),
            ("Occurrences", item.occurrence_count, False),
            ("Resolved source events", len(item.source_event_ids), False),
            ("Unresolved source events", len(item.unresolved_source_event_ids), False),
            ("Record IDs", ", ".join(item.record_ids) or None, True),
            ("Source event IDs", ", ".join(item.source_event_ids) or None, True),
            (
                "Unresolved source event IDs",
                ", ".join(item.unresolved_source_event_ids) or None,
                True,
            ),
        ]
        extra = ""
    elif isinstance(item, FileLoopEvidence):
        rows = [
            *common,
            ("File", item.display_path, True),
            ("Path resolution", item.path_resolution, True),
            ("Edits", item.edit_count, False),
            ("Resolved source events", len(item.source_event_ids), False),
            ("Unresolved source events", len(item.unresolved_source_event_ids), False),
            ("File activity IDs", ", ".join(item.file_activity_ids) or None, True),
            ("Source event IDs", ", ".join(item.source_event_ids) or None, True),
            (
                "Unresolved source event IDs",
                ", ".join(item.unresolved_source_event_ids) or None,
                True,
            ),
        ]
        extra = ""
    elif isinstance(item, ClassificationReferenceEvidence):
        rows = [
            *common,
            ("Classification", item.classification_id, True),
            ("Source event", item.source_event_id, True),
            ("Resolved node type", item.resolved_node_type, True),
            ("Resolved node", item.resolved_node_id, True),
        ]
        extra = ""
    else:
        raise TypeError("unsupported report evidence item")
    return card(definition_list(rows) + extra)


def ending_recurrence_actions(report: SessionReport) -> str:
    ending = report.ending
    ending_details = definition_list(
        [
            ("Status", ending.status, True),
            ("Reason", ending.reason, True),
            ("Unresolved ending signal", ending.unresolved_ending_signal, False),
            ("Evidence categories", ", ".join(ending.evidence_categories) or None, True),
            ("Resolved source events", len(ending.source_event_ids), False),
            ("Unresolved source events", len(ending.unresolved_source_event_ids), False),
            ("Late failed commands", len(ending.late_failed_command_ids), False),
            ("Late parse warnings", len(ending.late_parse_warning_ids), False),
            ("Missing final answer", ending.missing_final_answer, False),
            ("Resolution labels", ", ".join(ending.resolution_labels) or None, True),
        ]
    )
    statements = "".join(
        statement_block(title, rows)
        for title, rows in (
            ("Observations", report.observations),
            ("Review actions", report.review_actions),
            ("Limitations", report.limitations),
        )
    )
    ending_references = ending_reference_disclosure(ending)
    return (
        '<section class="section" aria-labelledby="ending-recurrence-actions">'
        '<h2 id="ending-recurrence-actions">Ending, recurrence, and actions</h2>'
        '<div class="grid">'
        f"{card(ending_details + ending_references, heading='Ending evidence')}"
        f"{card(project_context_summary(report), heading='Historical project context')}"
        "</div>"
        f"{recurrence_sections(report)}{recurrence_exclusions(report)}{statements}</section>"
    )


def ending_reference_disclosure(ending: ReportEnding) -> str:
    groups = (
        ("Resolved source events", ending.source_event_ids),
        ("Unresolved source events", ending.unresolved_source_event_ids),
        ("Late failed commands", ending.late_failed_command_ids),
        ("Late parse warnings", ending.late_parse_warning_ids),
    )
    rows = [
        [text(label), ", ".join(code(item) for item in identifiers)]
        for label, identifiers in groups
        if identifiers
    ]
    if not rows:
        return '<p class="muted">No exact ending evidence references are available.</p>'
    return disclosure(
        "Exact ending evidence references",
        table(
            ["Reference category", "Persisted IDs"],
            rows,
            caption="Exact ending evidence and unresolved references",
        ),
    )


def project_context_summary(report: SessionReport) -> str:
    context = report.project_context
    return definition_list(
        [
            ("Status", context.status, True),
            ("Reason", context.reason, True),
            ("Scope", context.scope_path, True),
            ("Scope source", context.scope_source, True),
            ("Window start", context.window_start, False),
            ("Evidence cutoff", context.evidence_cutoff, False),
            ("Failed commands", bounded_label(context.failed_commands), False),
            ("Failed tool results", bounded_label(context.failed_tool_results), False),
            ("Problematic files", bounded_label(context.problematic_files), False),
        ]
    )


def bounded_label(section: BoundedPatterns) -> str:
    if section.status == "unavailable":
        return f"Unavailable ({section.reason or 'unknown reason'})"
    return f"{section.displayed}/{section.total} displayed; {section.omitted} omitted"


def recurrence_sections(report: SessionReport) -> str:
    context = report.project_context
    return "".join(
        recurrence_disclosure(title, section)
        for title, section in (
            ("Recurring failed commands", context.failed_commands),
            ("Recurring failed tool results", context.failed_tool_results),
            ("Recurring problematic files", context.problematic_files),
        )
    )


def recurrence_disclosure(title: str, section: BoundedPatterns) -> str:
    label = f"{title} — {bounded_label(section)}"
    if section.status == "unavailable":
        body = f'<p class="notice">Unavailable: {code(section.reason)}</p>'
    elif not section.items:
        body = empty_state("No recurring patterns were available in this section.")
    else:
        body = table(
            [
                "Pattern",
                "Events",
                "Selected-session events",
                "Sessions",
                "Root families",
                "Top-level",
                "Sidechain",
                "Agents",
                "First observed",
                "Most recent",
            ],
            [pattern_row(row) for row in section.items],
            caption=title,
        )
    return disclosure(label, body)


def pattern_row(row) -> list[str]:
    if isinstance(row, FailedCommandPatternReport):
        label = code(row.command_display)
    elif isinstance(row, FailedToolPatternReport):
        label = code(row.tool_name) + " · " + code(row.fingerprint)
    elif isinstance(row, ProblematicFilePatternReport):
        label = code(row.display_path)
    else:
        raise TypeError("unsupported recurrence pattern")
    return [
        label,
        text(row.event_count),
        text(row.selected_session_event_count),
        text(row.session_count),
        text(row.root_family_count),
        text(row.top_level_session_count),
        text(row.sidechain_session_count),
        text(", ".join(row.agents) or "None"),
        display_value(row.first_at),
        display_value(row.most_recent_at),
    ]


def recurrence_exclusions(report: SessionReport) -> str:
    context = report.project_context
    groups = (
        ("Family exclusions", context.family_exclusions),
        ("Temporal exclusions", context.temporal_exclusions),
        ("Problematic-file analysis exclusions", context.problematic_file_analysis_exclusions),
    )
    return disclosure(
        "Historical context exclusions",
        "".join(
            table(
                ["Exclusion", "Count"],
                [[code(name), text(count)] for name, count in sorted(values.items())],
                caption=title,
            )
            for title, values in groups
        ),
    )


def statement_block(title: str, rows: list[ReportStatement]) -> str:
    return f"<h3>{text(title)}</h3>" + statement_list(
        (row.code, row.summary, row.evidence_ids) for row in rows
    )

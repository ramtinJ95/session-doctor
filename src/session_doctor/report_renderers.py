from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from session_doctor.report_models import (
    BoundedEvidence,
    BoundedPatterns,
    MessageSignalEvidence,
    ReportStatement,
    SessionReport,
)


def render_session_report(report: SessionReport, console: Console) -> None:
    console.print(f"[bold]Session report: {report.session.session_id}[/bold]")
    privacy_mode = (
        "included for displayed evidence" if report.privacy.message_text_included else "hidden"
    )
    console.print(f"Privacy: message text {privacy_mode}")
    overview = Table(title="Session", show_header=False)
    overview.add_column("Field")
    overview.add_column("Value")
    for name, value in (
        ("Agent", report.session.agent),
        ("Scope", "sidechain" if report.session.is_sidechain else "top-level"),
        ("Started", value_text(report.session.started_at)),
        ("Ended", value_text(report.session.ended_at)),
        ("Project", value_text(report.session.project_hint)),
        ("Analysis", report.analysis.status),
        ("Recovery", value_text(report.analysis.action)),
        (
            "Sequence activities",
            f"{report.sequence.total_resolved_activities} resolved, "
            f"{report.sequence.total_unresolved_activities} unresolved",
        ),
    ):
        overview.add_row(name, value)
    console.print(overview)

    scores = Table(title="Scores")
    scores.add_column("Name")
    scores.add_column("Value", justify="right")
    if report.scores:
        for score in report.scores:
            scores.add_row(score.name, f"{score.value:.3f}")
    else:
        scores.add_row("unavailable", report.analysis.status)
    console.print(scores)

    classifications = Table(title="Classifications")
    classifications.add_column("Label")
    classifications.add_column("Score")
    classifications.add_column("Evidence")
    if report.classifications:
        for row in report.classifications:
            classifications.add_row(row.label, f"{row.score:.3f}", row.evidence_summary)
    else:
        classifications.add_row("none/unavailable", "-", report.analysis.status)
    console.print(classifications)

    for section_name, section in report.evidence.items():
        render_evidence_section(section_name, section, console)

    ending = Table(title="Ending state", show_header=False)
    ending.add_column("Field")
    ending.add_column("Value")
    ending.add_row("Status", report.ending.status)
    ending.add_row("Unresolved signal", value_text(report.ending.unresolved_ending_signal))
    ending.add_row("Evidence categories", ", ".join(report.ending.evidence_categories) or "none")
    ending.add_row("Resolution labels", ", ".join(report.ending.resolution_labels) or "none")
    console.print(ending)

    render_project_context(report, console)
    render_statements("Observations", report.observations, console)
    render_statements("Review actions", report.review_actions, console)
    render_statements("Limitations", report.limitations, console)


def render_evidence_section(name: str, section: BoundedEvidence, console: Console) -> None:
    table = Table(title=name.replace("_", " ").title())
    table.add_column("Type")
    table.add_column("ID")
    table.add_column("Detail")
    if section.status == "unavailable":
        table.add_row("unavailable", "-", section.reason or "unknown")
    elif not section.items:
        table.add_row("none", "-", "No persisted evidence")
    else:
        for item in section.items:
            detail = evidence_detail(item)
            table.add_row(item.item_type, item.evidence_id, Text(detail))
    if section.omitted:
        table.caption = f"{section.omitted} of {section.total} items omitted by --limit"
    console.print(table)


def evidence_detail(item) -> str:
    if isinstance(item, MessageSignalEvidence):
        detail = f"{item.feature_name}; message={item.message_id}"
        if item.matched_message_id:
            detail += f"; matched={item.matched_message_id}"
        if item.text is not None:
            detail += f"; text={item.text}"
        return detail
    for field in ("command_display", "tool_name", "group_type", "display_path"):
        value = getattr(item, field, None)
        if value is not None:
            return str(value)
    return "persisted evidence reference"


def render_project_context(report: SessionReport, console: Console) -> None:
    context = Table(title="Historical project recurrence", show_header=False)
    context.add_column("Field")
    context.add_column("Value")
    context.add_row("Status", report.project_context.status)
    context.add_row("Reason", value_text(report.project_context.reason))
    context.add_row("Scope", value_text(report.project_context.scope_path))
    context.add_row("Window start", value_text(report.project_context.window_start))
    context.add_row("Evidence cutoff", value_text(report.project_context.evidence_cutoff))
    context.add_row("Failed commands", bounded_count(report.project_context.failed_commands))
    context.add_row(
        "Failed tool results", bounded_count(report.project_context.failed_tool_results)
    )
    context.add_row("Problematic files", bounded_count(report.project_context.problematic_files))
    console.print(context)


def bounded_count(section: BoundedPatterns) -> str:
    if section.status == "unavailable":
        return f"unavailable ({section.reason})"
    return f"{section.displayed}/{section.total} displayed"


def render_statements(title: str, statements: list[ReportStatement], console: Console) -> None:
    table = Table(title=title)
    table.add_column("Code")
    table.add_column("Summary")
    if statements:
        for statement in statements:
            table.add_row(statement.code, statement.summary)
    else:
        table.add_row("none", "No available statements")
    console.print(table)


def render_session_report_markdown(report: SessionReport) -> str:
    lines = [
        f"# Session report: `{report.session.session_id}`",
        "",
        "## Privacy",
        "",
        f"- Message text included: `{str(report.privacy.message_text_included).lower()}`",
        f"- Disclosure scope: `{report.privacy.disclosure_scope}`",
        "",
        "## Session and analysis",
        "",
        f"- Agent: `{report.session.agent}`",
        f"- Scope: `{'sidechain' if report.session.is_sidechain else 'top-level'}`",
        f"- Started: {markdown_value(report.session.started_at)}",
        f"- Ended: {markdown_value(report.session.ended_at)}",
        f"- Project hint: {markdown_value(report.session.project_hint)}",
        f"- Analysis: `{report.analysis.status}`",
        f"- Recovery action: {markdown_value(report.analysis.action)}",
        "- Sequence activities: "
        f"{report.sequence.total_resolved_activities} resolved, "
        f"{report.sequence.total_unresolved_activities} unresolved "
        "(`source_record_order`)",
        "",
        "## Scores",
        "",
    ]
    if report.scores:
        lines.extend(["| Score | Value |", "| --- | ---: |"])
        lines.extend(f"| `{row.name}` | {row.value:.3f} |" for row in report.scores)
    else:
        lines.append(f"Unavailable: `{report.analysis.status}`.")
    lines.extend(["", "## Classifications", ""])
    if report.classifications:
        lines.extend(["| Label | Score | Confidence | Evidence |", "| --- | ---: | ---: | --- |"])
        lines.extend(
            f"| `{row.label}` | {row.score:.3f} | {row.confidence:.3f} | "
            f"{escape_table(row.evidence_summary)} |"
            for row in report.classifications
        )
    else:
        lines.append(f"None or unavailable: `{report.analysis.status}`.")

    lines.extend(["", "## Evidence", ""])
    for name, section in report.evidence.items():
        lines.extend([f"### {name.replace('_', ' ').title()}", ""])
        if section.status == "unavailable":
            lines.append(f"Unavailable: `{section.reason}`.")
        elif not section.items:
            lines.append("No persisted evidence.")
        else:
            for item in section.items:
                lines.append(
                    f"- `{item.evidence_id}` ({item.item_type}): "
                    f"{escape_markdown_text(evidence_detail(item))}"
                )
            if section.omitted:
                lines.append(f"- {section.omitted} of {section.total} items omitted by `--limit`.")
        lines.append("")

    lines.extend(
        [
            "## Ending state",
            "",
            f"- Status: `{report.ending.status}`",
            f"- Unresolved signal: {markdown_value(report.ending.unresolved_ending_signal)}",
            "- Evidence categories: "
            + (", ".join(f"`{item}`" for item in report.ending.evidence_categories) or "none"),
            "- Resolution labels: "
            + (", ".join(f"`{item}`" for item in report.ending.resolution_labels) or "none"),
            "",
            "## Historical project recurrence",
            "",
            f"- Status: `{report.project_context.status}`",
            f"- Reason: {markdown_value(report.project_context.reason)}",
            f"- Scope: {markdown_value(report.project_context.scope_path)}",
            f"- Failed commands: {bounded_count(report.project_context.failed_commands)}",
            f"- Failed tool results: {bounded_count(report.project_context.failed_tool_results)}",
            f"- Problematic files: {bounded_count(report.project_context.problematic_files)}",
            "",
        ]
    )
    append_statement_markdown(lines, "Observations", report.observations)
    append_statement_markdown(lines, "Review actions", report.review_actions)
    append_statement_markdown(lines, "Limitations", report.limitations)
    return "\n".join(lines).rstrip() + "\n"


def append_statement_markdown(
    lines: list[str], title: str, statements: list[ReportStatement]
) -> None:
    lines.extend([f"## {title}", ""])
    if statements:
        lines.extend(f"- **`{row.code}`**: {row.summary}" for row in statements)
    else:
        lines.append("No available statements.")
    lines.append("")


def markdown_value(value: object | None) -> str:
    return "`null`" if value is None else f"`{value}`"


def value_text(value: object | None) -> str:
    return "-" if value is None else str(value)


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def escape_markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("\n", " ")

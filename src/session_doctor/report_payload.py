from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from math import isfinite

from session_doctor.analysis.classification_constants import NEGATIVE_LABELS
from session_doctor.diagnostic_models import DiagnosticSnapshot
from session_doctor.ids import stable_id
from session_doctor.privacy import (
    display_file_path,
    display_project_hint,
    public_fingerprint,
    redact_command_for_display,
)
from session_doctor.report_models import (
    BoundedEvidence,
    BoundedPatterns,
    ClassificationReferenceEvidence,
    CommandFailureEvidence,
    EvidenceItem,
    FailedCommandPatternReport,
    FailedToolPatternReport,
    FailureGroupEvidence,
    FileLoopEvidence,
    MessageSignalEvidence,
    PatternItem,
    ProblematicFilePatternReport,
    ProjectContextReport,
    ReportAnalysis,
    ReportClassification,
    ReportEnding,
    ReportPrivacy,
    ReportScore,
    ReportSession,
    ReportStatement,
    ReportSummary,
    SessionReport,
    ToolFailureEvidence,
)
from session_doctor.schemas import SessionFeature
from session_doctor.store.aggregate_queries import SCORE_NAMES

MESSAGE_SECTIONS = {
    "repeated_requests": "repeat_request_similarity",
    "corrections": "correction_marker",
    "frustration_markers": "frustration_marker",
    "scope_boundaries": "scope_boundary_marker",
    "ambiguity_markers": "ambiguity_marker",
    "stop_or_pause_markers": "stop_or_pause_marker",
}
EVIDENCE_SECTION_ORDER = (
    *MESSAGE_SECTIONS,
    "command_failures",
    "tool_failures",
    "repeated_failures",
    "repeated_file_edits",
    "classification_evidence",
)
FAILURE_GROUP_TYPES = {
    "command_stderr_hash",
    "command_stdout_hash",
    "failed_command_identity",
    "tool_output_hash",
}


def build_session_report(
    snapshot: DiagnosticSnapshot,
    *,
    limit: int = 10,
    show_text: bool = False,
) -> SessionReport:
    if limit < 1:
        raise ValueError("limit must be positive")
    session = snapshot.normalized.session
    project_hint, project_hint_source = display_project_hint(session.project_path, session.cwd)
    current = snapshot.analysis.compatibility.value == "current"
    evidence = (
        build_evidence(snapshot, limit=limit, show_text=show_text)
        if current
        else {
            section: unavailable_evidence(f"analysis_{snapshot.analysis.compatibility.value}")
            for section in EVIDENCE_SECTION_ORDER
        }
    )
    scores = score_rows(snapshot) if current else []
    classifications = classification_rows(snapshot) if current else []
    ending = ending_report(snapshot) if current else unavailable_ending(snapshot)
    project_context = project_context_report(snapshot, limit)
    observations = observation_rows(snapshot, evidence, ending, project_context) if current else []
    review_actions = review_action_rows(evidence, ending) if current else []
    limitations = limitation_rows(snapshot, project_context)
    return SessionReport(
        session=ReportSession(
            session_id=session.session_id,
            agent=session.agent_name.value,
            is_sidechain=session.is_sidechain,
            parent_session_id=session.parent_session_id,
            child_session_ids=sorted(
                row.session_id
                for row in snapshot.topology_references
                if row.relationship == "child"
            ),
            started_at=session.started_at,
            ended_at=session.ended_at,
            project_hint=project_hint,
            project_hint_source=project_hint_source,
            model_provider=session.model_provider,
            model=session.model,
            agent_version=session.agent_version,
        ),
        privacy=ReportPrivacy(
            message_text_included=show_text,
            disclosure_scope="displayed_evidence_messages" if show_text else "none",
        ),
        analysis=ReportAnalysis(
            status=snapshot.analysis.compatibility.value,
            current_analyzer_version=snapshot.analysis.current_analyzer_version,
            observed_analyzer_version=snapshot.analysis.observed_analyzer_version,
            analysis_run_id=snapshot.analysis.analysis_run_id,
            action=snapshot.analysis.action,
        ),
        summary=ReportSummary(
            raw_events=len(snapshot.normalized.raw_events),
            messages=len(snapshot.normalized.messages),
            tool_calls=len(snapshot.normalized.tool_calls),
            tool_results=len(snapshot.normalized.tool_results),
            command_runs=len(snapshot.normalized.command_runs),
            file_activities=len(snapshot.normalized.file_activities),
            parse_warnings=len(snapshot.normalized.parse_warnings),
            parse_warning_codes=sorted(
                {
                    code
                    for warning in snapshot.normalized.parse_warnings
                    if isinstance((code := warning.metadata.get("code")), str)
                }
            ),
        ),
        scores=scores,
        classifications=classifications,
        evidence=evidence,
        ending=ending,
        project_context=project_context,
        observations=observations,
        review_actions=review_actions,
        limitations=limitations,
    )


def score_rows(snapshot: DiagnosticSnapshot) -> list[ReportScore]:
    features = {row.feature_name: row for row in snapshot.analysis.session_features}
    rows: list[ReportScore] = []
    raw_ids = snapshot.indexes.raw_events_by_id
    for name in SCORE_NAMES:
        feature = features.get(name)
        if feature is None:
            continue
        source_ids = string_list(feature.evidence.get("source_event_ids"))
        rows.append(
            ReportScore(
                name=name,
                value=round(feature.score, 3),
                component_values=numeric_mapping(feature.metadata.get("component_values")),
                component_weights=numeric_mapping(feature.metadata.get("component_weights")),
                contributions=numeric_mapping(feature.metadata.get("contributions")),
                source_event_ids=[event_id for event_id in source_ids if event_id in raw_ids],
                unresolved_source_event_ids=[
                    event_id for event_id in source_ids if event_id not in raw_ids
                ],
            )
        )
    return rows


def classification_rows(snapshot: DiagnosticSnapshot) -> list[ReportClassification]:
    raw_ids = snapshot.indexes.raw_events_by_id
    classifications = sorted(
        snapshot.analysis.classifications,
        key=lambda row: (row.label, row.session_classification_id),
    )
    classifications.sort(key=lambda row: row.score, reverse=True)
    classifications.sort(key=lambda row: row.label in NEGATIVE_LABELS, reverse=True)
    return [
        ReportClassification(
            classification_id=row.session_classification_id,
            label=row.label,
            score=round(row.score, 3),
            confidence=round(row.confidence, 3),
            evidence_summary=row.evidence_summary,
            source_event_ids=[
                event_id for event_id in row.evidence_event_ids if event_id in raw_ids
            ],
            unresolved_source_event_ids=[
                event_id for event_id in row.evidence_event_ids if event_id not in raw_ids
            ],
        )
        for row in classifications
    ]


def build_evidence(
    snapshot: DiagnosticSnapshot,
    *,
    limit: int,
    show_text: bool,
) -> dict[str, BoundedEvidence]:
    result: dict[str, BoundedEvidence] = {}
    for section, feature_name in MESSAGE_SECTIONS.items():
        items = message_signal_items(snapshot, feature_name, show_text)
        result[section] = bounded_evidence(items, limit)
    result["command_failures"] = bounded_evidence(command_failure_items(snapshot), limit)
    result["tool_failures"] = bounded_evidence(tool_failure_items(snapshot), limit)
    result["repeated_failures"] = bounded_evidence(failure_group_items(snapshot), limit)
    result["repeated_file_edits"] = bounded_evidence(file_loop_items(snapshot), limit)
    result["classification_evidence"] = bounded_evidence(
        classification_reference_items(snapshot), limit
    )
    return result


def message_signal_items(
    snapshot: DiagnosticSnapshot,
    feature_name: str,
    show_text: bool,
) -> list[MessageSignalEvidence]:
    items: list[MessageSignalEvidence] = []
    for feature in snapshot.analysis.message_features:
        if feature.feature_name != feature_name:
            continue
        message = snapshot.indexes.messages_by_id.get(feature.message_id)
        matched_message_id = string_value(feature.evidence.get("matched_message_id"))
        items.append(
            MessageSignalEvidence(
                evidence_id=feature.message_feature_id,
                feature_id=feature.message_feature_id,
                feature_name=feature.feature_name,
                message_id=feature.message_id,
                source_event_id=feature.source_event_id,
                role=message.role.value if message else None,
                timestamp=message.timestamp if message else None,
                score=round(feature.score, 3),
                matched_message_id=matched_message_id,
                matched_source_event_id=string_value(
                    feature.evidence.get("matched_source_event_id")
                ),
                similarity_score=finite_number(feature.evidence.get("similarity_score")),
                text=message.text if show_text and message is not None else None,
            )
        )
    return sorted(items, key=lambda item: message_item_key(snapshot, item))


def message_item_key(
    snapshot: DiagnosticSnapshot,
    item: MessageSignalEvidence,
) -> tuple[bool, int, bool, datetime, str, str]:
    event = snapshot.indexes.raw_events_by_id.get(item.source_event_id or "")
    return (
        event is None,
        event.record_index if event else 0,
        item.timestamp is None,
        item.timestamp or datetime.min,
        item.message_id,
        item.feature_name,
    )


def command_failure_items(snapshot: DiagnosticSnapshot) -> list[CommandFailureEvidence]:
    feature = session_feature(snapshot, "failed_command_count")
    ids = string_list(feature.evidence.get("command_run_ids")) if feature else []
    items: list[CommandFailureEvidence] = []
    for command_id in ids:
        command = snapshot.indexes.command_runs_by_id.get(command_id)
        if command is None:
            continue
        items.append(
            CommandFailureEvidence(
                evidence_id=stable_id("report-command-failure", command.command_run_id),
                command_run_id=command.command_run_id,
                source_event_id=command.source_event_id,
                command_display=redact_command_for_display(command.command_display),
                exit_code=command.exit_code,
                fingerprint=public_fingerprint("command", command.command_identity_hash),
            )
        )
    return sorted(
        items, key=lambda item: record_item_key(snapshot, item.source_event_id, item.command_run_id)
    )


def tool_failure_items(snapshot: DiagnosticSnapshot) -> list[ToolFailureEvidence]:
    feature = session_feature(snapshot, "failed_tool_result_count")
    ids = string_list(feature.evidence.get("tool_result_ids")) if feature else []
    items: list[ToolFailureEvidence] = []
    for result_id in ids:
        result = snapshot.indexes.tool_results_by_id.get(result_id)
        if result is None:
            continue
        call = snapshot.indexes.tool_calls_by_id.get(result.tool_call_id or "")
        fingerprint = (
            public_fingerprint("tool-result", result.output_hash) if result.output_hash else None
        )
        items.append(
            ToolFailureEvidence(
                evidence_id=stable_id("report-tool-failure", result.tool_result_id),
                tool_result_id=result.tool_result_id,
                tool_call_id=result.tool_call_id,
                source_event_id=result.source_event_id,
                tool_name=call.name if call else None,
                output_length=result.output_length,
                fingerprint=fingerprint,
            )
        )
    return sorted(
        items, key=lambda item: record_item_key(snapshot, item.source_event_id, item.tool_result_id)
    )


def failure_group_items(snapshot: DiagnosticSnapshot) -> list[FailureGroupEvidence]:
    feature = session_feature(snapshot, "repeated_failure_count")
    groups = feature.evidence.get("groups") if feature else None
    if not isinstance(groups, list):
        return []
    items: list[FailureGroupEvidence] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_type = string_value(group.get("group_type"))
        private_key = string_value(group.get("key"))
        record_ids = string_list(group.get("record_ids"))
        source_event_ids = string_list(group.get("source_event_ids"))
        if group_type not in FAILURE_GROUP_TYPES or private_key is None or len(record_ids) < 2:
            continue
        known_record_ids = (
            snapshot.indexes.tool_results_by_id
            if group_type == "tool_output_hash"
            else snapshot.indexes.command_runs_by_id
        )
        fingerprint = public_fingerprint(group_type, private_key)
        items.append(
            FailureGroupEvidence(
                evidence_id=stable_id("report-failure-group", group_type, private_key),
                group_type=group_type,
                fingerprint=fingerprint,
                occurrence_count=len(record_ids),
                record_ids=[record_id for record_id in record_ids if record_id in known_record_ids],
                source_event_ids=[
                    event_id
                    for event_id in source_event_ids
                    if event_id in snapshot.indexes.raw_events_by_id
                ],
            )
        )
    items.sort(key=lambda item: (item.group_type, item.fingerprint))
    items.sort(key=lambda item: item.occurrence_count, reverse=True)
    return items


def file_loop_items(snapshot: DiagnosticSnapshot) -> list[FileLoopEvidence]:
    feature = session_feature(snapshot, "same_file_edited_repeatedly_count")
    paths = string_list(feature.evidence.get("paths")) if feature else []
    event_map = feature.evidence.get("source_event_ids_by_path") if feature else None
    items: list[FileLoopEvidence] = []
    for path in paths:
        source_event_ids = string_list(event_map.get(path)) if isinstance(event_map, dict) else []
        activities = [
            row
            for row in snapshot.normalized.file_activities
            if path in {row.canonical_path, row.project_relative_path, row.normalized_path}
            and (not source_event_ids or row.source_event_id in source_event_ids)
        ]
        if len(activities) < 2:
            continue
        first = activities[0]
        display_path = display_file_path(
            project_relative_path=first.project_relative_path,
            normalized_path=first.normalized_path,
            canonical_path=first.canonical_path,
        )
        items.append(
            FileLoopEvidence(
                evidence_id=stable_id("report-file-loop", path),
                display_path=display_path,
                path_resolution=first.path_resolution,
                edit_count=len(activities),
                file_activity_ids=sorted(row.file_activity_id for row in activities),
                source_event_ids=sorted(
                    {row.source_event_id for row in activities if row.source_event_id is not None}
                ),
            )
        )
    return sorted(items, key=lambda item: (item.display_path, item.evidence_id))


def classification_reference_items(
    snapshot: DiagnosticSnapshot,
) -> list[ClassificationReferenceEvidence]:
    classification_order = {
        row.classification_id: index for index, row in enumerate(classification_rows(snapshot))
    }
    items = [
        ClassificationReferenceEvidence(
            evidence_id=stable_id(
                "report-classification-reference",
                classification.session_classification_id,
                event_id,
            ),
            classification_id=classification.session_classification_id,
            source_event_id=event_id,
            resolved_node_type="raw_event"
            if event_id in snapshot.indexes.raw_events_by_id
            else None,
            resolved_node_id=event_id if event_id in snapshot.indexes.raw_events_by_id else None,
        )
        for classification in snapshot.analysis.classifications
        for event_id in classification.evidence_event_ids
    ]
    return sorted(
        items,
        key=lambda item: (
            classification_order.get(item.classification_id, len(classification_order)),
            record_item_key(snapshot, item.source_event_id, item.evidence_id),
        ),
    )


def ending_report(snapshot: DiagnosticSnapshot) -> ReportEnding:
    feature = session_feature(snapshot, "unresolved_ending_signal")
    if feature is None:
        return ReportEnding(
            status="available",
            reason=None,
            unresolved_ending_signal=None,
            evidence_categories=[],
            source_event_ids=[],
            unresolved_source_event_ids=[],
            late_failed_command_ids=[],
            late_parse_warning_ids=[],
            missing_final_answer=None,
            resolution_labels=resolution_labels(snapshot),
        )
    source_ids = string_list(feature.evidence.get("source_event_ids"))
    categories = sorted(
        key
        for key in (
            "late_message_features",
            "late_failed_command_ids",
            "late_parse_warning_ids",
            "missing_final_answer",
        )
        if key in feature.evidence
    )
    return ReportEnding(
        status="available",
        reason=None,
        unresolved_ending_signal=feature.feature_value.lower() == "true",
        evidence_categories=categories,
        source_event_ids=[
            event_id for event_id in source_ids if event_id in snapshot.indexes.raw_events_by_id
        ],
        unresolved_source_event_ids=[
            event_id for event_id in source_ids if event_id not in snapshot.indexes.raw_events_by_id
        ],
        late_failed_command_ids=[
            command_id
            for command_id in string_list(feature.evidence.get("late_failed_command_ids"))
            if command_id in snapshot.indexes.command_runs_by_id
        ],
        late_parse_warning_ids=[
            warning_id
            for warning_id in string_list(feature.evidence.get("late_parse_warning_ids"))
            if any(row.warning_id == warning_id for row in snapshot.normalized.parse_warnings)
        ],
        missing_final_answer=boolean_value(feature.evidence.get("missing_final_answer")),
        resolution_labels=resolution_labels(snapshot),
    )


def unavailable_ending(snapshot: DiagnosticSnapshot) -> ReportEnding:
    return ReportEnding(
        status="unavailable",
        reason=f"analysis_{snapshot.analysis.compatibility.value}",
        unresolved_ending_signal=None,
        evidence_categories=[],
        source_event_ids=[],
        unresolved_source_event_ids=[],
        late_failed_command_ids=[],
        late_parse_warning_ids=[],
        missing_final_answer=None,
        resolution_labels=[],
    )


def resolution_labels(snapshot: DiagnosticSnapshot) -> list[str]:
    return sorted(
        row.label
        for row in snapshot.analysis.classifications
        if row.label in {"resolved_after_corrections", "healthy"}
    )


def project_context_report(snapshot: DiagnosticSnapshot, limit: int) -> ProjectContextReport:
    context = snapshot.recurrence
    failed_commands = [
        FailedCommandPatternReport(
            pattern_id=row.pattern_id,
            event_count=row.evidence.event_count,
            selected_session_event_count=row.evidence.selected_session_event_count,
            session_count=row.evidence.session_count,
            root_family_count=row.evidence.root_family_count,
            top_level_session_count=row.evidence.top_level_session_count,
            sidechain_session_count=row.evidence.sidechain_session_count,
            agents=list(row.evidence.agents),
            first_at=row.evidence.first_at,
            most_recent_at=row.evidence.most_recent_at,
            command_display=row.command_display,
        )
        for row in context.failed_commands
    ]
    failed_tools = [
        FailedToolPatternReport(
            pattern_id=row.pattern_id,
            event_count=row.evidence.event_count,
            selected_session_event_count=row.evidence.selected_session_event_count,
            session_count=row.evidence.session_count,
            root_family_count=row.evidence.root_family_count,
            top_level_session_count=row.evidence.top_level_session_count,
            sidechain_session_count=row.evidence.sidechain_session_count,
            agents=list(row.evidence.agents),
            first_at=row.evidence.first_at,
            most_recent_at=row.evidence.most_recent_at,
            tool_name=row.tool_name,
            fingerprint=row.fingerprint,
        )
        for row in context.failed_tool_results
    ]
    problematic_files = [
        ProblematicFilePatternReport(
            pattern_id=row.pattern_id,
            event_count=row.evidence.event_count,
            selected_session_event_count=row.evidence.selected_session_event_count,
            session_count=row.evidence.session_count,
            root_family_count=row.evidence.root_family_count,
            top_level_session_count=row.evidence.top_level_session_count,
            sidechain_session_count=row.evidence.sidechain_session_count,
            agents=list(row.evidence.agents),
            first_at=row.evidence.first_at,
            most_recent_at=row.evidence.most_recent_at,
            display_path=row.display_path,
        )
        for row in context.problematic_files
    ]
    return ProjectContextReport(
        status=context.status,
        reason=context.reason,
        scope_path=context.scope_path,
        scope_source=context.scope_source,
        window_start=context.window_start,
        evidence_cutoff=context.evidence_cutoff,
        family_exclusions={
            "cross_agent_parent": context.family_exclusions.cross_agent_parent,
            "cycle": context.family_exclusions.cycle,
            "orphan_parent": context.family_exclusions.orphan_parent,
        },
        temporal_exclusions={
            "after_cutoff_events": context.temporal_exclusions.after_cutoff_events,
            "after_cutoff_sessions": context.temporal_exclusions.after_cutoff_sessions,
            "before_window_events": context.temporal_exclusions.before_window_events,
            "before_window_sessions": context.temporal_exclusions.before_window_sessions,
            "untimed_events": context.temporal_exclusions.untimed_events,
            "untimed_sessions": context.temporal_exclusions.untimed_sessions,
        },
        problematic_file_analysis_exclusions={
            "missing": context.problematic_file_analysis_exclusions.missing,
            "stale": context.problematic_file_analysis_exclusions.stale,
        },
        failed_commands=bounded_patterns(failed_commands, limit, context.status, context.reason),
        failed_tool_results=bounded_patterns(failed_tools, limit, context.status, context.reason),
        problematic_files=bounded_patterns(
            problematic_files,
            limit,
            context.problematic_files_status if context.status == "available" else context.status,
            context.problematic_files_reason or context.reason,
        ),
    )


def observation_rows(
    snapshot: DiagnosticSnapshot,
    evidence: dict[str, BoundedEvidence],
    ending: ReportEnding,
    project_context: ProjectContextReport,
) -> list[ReportStatement]:
    rows: list[ReportStatement] = []
    repeated = evidence["repeated_requests"]
    if repeated.total:
        rows.append(
            ReportStatement(
                code="repeated_requests_observed",
                summary=f"Repeated request evidence is present across {repeated.total} messages.",
                evidence_ids=[item.evidence_id for item in repeated.items],
            )
        )
    failures = evidence["repeated_failures"]
    if failures.total:
        rows.append(
            ReportStatement(
                code="repeated_failures_observed",
                summary=f"Persisted analysis contains {failures.total} repeated failure groups.",
                evidence_ids=[item.evidence_id for item in failures.items],
            )
        )
    if ending.unresolved_ending_signal:
        rows.append(
            ReportStatement(
                code="unresolved_ending_observed",
                summary="The stored ending signal is consistent with unresolved late evidence.",
                evidence_ids=ending.source_event_ids,
            )
        )
    recurrence_items = [
        *project_context.failed_commands.items,
        *project_context.failed_tool_results.items,
        *project_context.problematic_files.items,
    ]
    if recurrence_items:
        roots = max(item.root_family_count for item in recurrence_items)
        rows.append(
            ReportStatement(
                code="project_recurrence_observed",
                summary=(
                    "This session contributes to an observed project recurrence spanning "
                    f"up to {roots} root families."
                ),
                evidence_ids=[item.pattern_id for item in recurrence_items],
            )
        )
    return rows


def review_action_rows(
    evidence: dict[str, BoundedEvidence], ending: ReportEnding
) -> list[ReportStatement]:
    rows: list[ReportStatement] = []
    if evidence["repeated_requests"].total:
        rows.append(
            ReportStatement(
                code="review_repeated_requests",
                summary="Review the displayed repeated-request evidence and its matched prior IDs.",
                evidence_ids=[item.evidence_id for item in evidence["repeated_requests"].items],
            )
        )
    if evidence["repeated_failures"].total:
        rows.append(
            ReportStatement(
                code="review_repeated_failures",
                summary="Compare the first and last records in each displayed failure group.",
                evidence_ids=[item.evidence_id for item in evidence["repeated_failures"].items],
            )
        )
    if ending.unresolved_ending_signal:
        rows.append(
            ReportStatement(
                code="review_session_ending",
                summary="Review the stored late evidence before interpreting the session ending.",
                evidence_ids=ending.source_event_ids,
            )
        )
    return rows


def limitation_rows(
    snapshot: DiagnosticSnapshot,
    project_context: ProjectContextReport,
) -> list[ReportStatement]:
    rows: list[ReportStatement] = []
    if snapshot.analysis.compatibility.value != "current":
        rows.append(
            ReportStatement(
                code=f"analysis_{snapshot.analysis.compatibility.value}",
                summary=(
                    "Analysis-derived sections are unavailable until the session is analyzed "
                    "with the current analyzer."
                ),
                evidence_ids=[],
            )
        )
    unresolved_count = sum(
        len(value) for value in vars(snapshot.unresolved).values() if isinstance(value, tuple)
    )
    if unresolved_count:
        rows.append(
            ReportStatement(
                code="unresolved_references",
                summary=(
                    f"{unresolved_count} persisted reference occurrences could not be resolved."
                ),
                evidence_ids=[],
            )
        )
    analysis_reference_ids = unresolved_analysis_reference_ids(snapshot)
    if analysis_reference_ids:
        rows.append(
            ReportStatement(
                code="unresolved_analysis_references",
                summary=(
                    f"{len(analysis_reference_ids)} analysis evidence record occurrences could "
                    "not be resolved."
                ),
                evidence_ids=analysis_reference_ids,
            )
        )
    if project_context.status == "unavailable":
        rows.append(
            ReportStatement(
                code="project_context_unavailable",
                summary=f"Historical project recurrence is unavailable: {project_context.reason}.",
                evidence_ids=[],
            )
        )
    if project_context.problematic_files.status == "unavailable":
        rows.append(
            ReportStatement(
                code="problematic_files_unavailable",
                summary=(
                    "Problematic-file recurrence is unavailable without current selected-session "
                    "analysis."
                ),
                evidence_ids=[],
            )
        )
    return rows


def bounded_evidence(items: Sequence[EvidenceItem], limit: int) -> BoundedEvidence:
    displayed = list(items[:limit])
    return BoundedEvidence(
        status="available",
        reason=None,
        total=len(items),
        displayed=len(displayed),
        omitted=len(items) - len(displayed),
        items=displayed,
    )


def unavailable_evidence(reason: str) -> BoundedEvidence:
    return BoundedEvidence(
        status="unavailable", reason=reason, total=0, displayed=0, omitted=0, items=[]
    )


def bounded_patterns(
    items: Sequence[PatternItem],
    limit: int,
    status: str,
    reason: str | None,
) -> BoundedPatterns:
    if status == "unavailable":
        return BoundedPatterns(
            status="unavailable", reason=reason, total=0, displayed=0, omitted=0, items=[]
        )
    displayed = list(items[:limit])
    return BoundedPatterns(
        status="available",
        reason=None,
        total=len(items),
        displayed=len(displayed),
        omitted=len(items) - len(displayed),
        items=displayed,
    )


def session_feature(snapshot: DiagnosticSnapshot, name: str) -> SessionFeature | None:
    return next(
        (row for row in snapshot.analysis.session_features if row.feature_name == name), None
    )


def unresolved_analysis_reference_ids(snapshot: DiagnosticSnapshot) -> list[str]:
    unresolved: list[str] = []
    command_feature = session_feature(snapshot, "failed_command_count")
    if command_feature:
        unresolved.extend(
            record_id
            for record_id in string_list(command_feature.evidence.get("command_run_ids"))
            if record_id not in snapshot.indexes.command_runs_by_id
        )
    tool_feature = session_feature(snapshot, "failed_tool_result_count")
    if tool_feature:
        unresolved.extend(
            record_id
            for record_id in string_list(tool_feature.evidence.get("tool_result_ids"))
            if record_id not in snapshot.indexes.tool_results_by_id
        )
    ending_feature = session_feature(snapshot, "unresolved_ending_signal")
    if ending_feature:
        unresolved.extend(
            record_id
            for record_id in string_list(ending_feature.evidence.get("late_failed_command_ids"))
            if record_id not in snapshot.indexes.command_runs_by_id
        )
        warning_ids = {row.warning_id for row in snapshot.normalized.parse_warnings}
        unresolved.extend(
            warning_id
            for warning_id in string_list(ending_feature.evidence.get("late_parse_warning_ids"))
            if warning_id not in warning_ids
        )
    file_feature = session_feature(snapshot, "same_file_edited_repeatedly_count")
    if file_feature:
        for path in string_list(file_feature.evidence.get("paths")):
            resolved_count = sum(
                path in {row.canonical_path, row.project_relative_path, row.normalized_path}
                for row in snapshot.normalized.file_activities
            )
            if resolved_count < 2:
                unresolved.append(stable_id("unresolved-file-loop", path))
    return unresolved


def record_item_key(
    snapshot: DiagnosticSnapshot, source_event_id: str | None, record_id: str
) -> tuple[bool, int, str]:
    event = snapshot.indexes.raw_events_by_id.get(source_event_id or "")
    return event is None, event.record_index if event else 0, record_id


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def boolean_value(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def finite_number(value: object) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    number = float(value)
    return round(number, 3) if isfinite(number) else None


def numeric_mapping(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        key: number
        for key, raw in sorted(value.items())
        if isinstance(key, str) and (number := finite_number(raw)) is not None
    }

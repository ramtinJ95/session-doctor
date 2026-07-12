from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from session_doctor.diagnostic_models import AnalysisCompatibility, DiagnosticSnapshot
from session_doctor.ids import stable_id
from session_doctor.report_models import (
    ClassificationReferenceEvidence,
    CommandFailureEvidence,
    EvidenceItem,
    FailureGroupEvidence,
    FileLoopEvidence,
    MessageSignalEvidence,
    ReportClassification,
    ReportEnding,
    ReportScore,
    SequenceActivityCounts,
    SequenceBin,
    SequenceEvidenceMarker,
    SessionSequence,
    ToolFailureEvidence,
    UnresolvedSequenceMarkerCount,
)
from session_doctor.schemas import NormalizedRole

MAX_SEQUENCE_BINS = 80
ActivityCategory = Literal[
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "tool_failure",
    "command_success",
    "command_failure",
    "command_unknown",
    "file_activity",
    "parse_warning",
]
ACTIVITY_CATEGORIES: tuple[ActivityCategory, ...] = (
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "tool_failure",
    "command_success",
    "command_failure",
    "command_unknown",
    "file_activity",
    "parse_warning",
)


@dataclass(frozen=True)
class SequenceActivity:
    category: ActivityCategory
    record_index: int | None


@dataclass(frozen=True)
class MarkerReference:
    category: str
    evidence_id: str
    source_event_id: str | None


def build_session_sequence(
    snapshot: DiagnosticSnapshot,
    *,
    scores: list[ReportScore],
    classifications: list[ReportClassification],
    evidence: dict[str, list[EvidenceItem]],
    ending: ReportEnding,
) -> SessionSequence:
    activities = activity_rows(snapshot)
    raw_events = tuple(snapshot.normalized.raw_events)
    first_record_index = min((row.record_index for row in raw_events), default=None)
    last_record_index = max((row.record_index for row in raw_events), default=None)
    resolved_counts = Counter(row.category for row in activities if row.record_index is not None)
    unresolved_counts = Counter(row.category for row in activities if row.record_index is None)
    markers, unresolved_markers = evidence_marker_rows(
        snapshot,
        scores=scores,
        classifications=classifications,
        evidence=evidence,
        ending=ending,
    )
    return SessionSequence(
        first_record_index=first_record_index,
        last_record_index=last_record_index,
        total_resolved_activities=sum(resolved_counts.values()),
        total_unresolved_activities=sum(unresolved_counts.values()),
        resolved_activity_counts=activity_counts(resolved_counts),
        unresolved_activity_counts=activity_counts(unresolved_counts),
        bins=sequence_bins(activities, first_record_index, last_record_index),
        evidence_markers=markers,
        unresolved_evidence_markers=[
            UnresolvedSequenceMarkerCount(category=category, count=count)
            for category, count in sorted(unresolved_markers.items())
        ],
    )


def activity_rows(snapshot: DiagnosticSnapshot) -> list[SequenceActivity]:
    event_indexes = {event.event_id: event.record_index for event in snapshot.normalized.raw_events}
    rows: list[SequenceActivity] = []
    for message in snapshot.normalized.messages:
        category: ActivityCategory | None = None
        if message.role is NormalizedRole.USER:
            category = "user_message"
        elif message.role is NormalizedRole.ASSISTANT:
            category = "assistant_message"
        if category is not None:
            rows.append(
                SequenceActivity(category, event_indexes.get(message.source_event_id or ""))
            )
    rows.extend(
        SequenceActivity("tool_call", event_indexes.get(row.source_event_id or ""))
        for row in snapshot.normalized.tool_calls
    )
    rows.extend(
        SequenceActivity(
            "tool_failure" if row.is_error is True else "tool_result",
            event_indexes.get(row.source_event_id or ""),
        )
        for row in snapshot.normalized.tool_results
    )
    for command in snapshot.normalized.command_runs:
        interrupted = (
            command.metadata.get("cancelled") is True or command.metadata.get("interrupted") is True
        )
        if interrupted or (command.exit_code is not None and command.exit_code != 0):
            category = "command_failure"
        elif command.exit_code == 0:
            category = "command_success"
        else:
            category = "command_unknown"
        rows.append(SequenceActivity(category, event_indexes.get(command.source_event_id or "")))
    rows.extend(
        SequenceActivity("file_activity", event_indexes.get(row.source_event_id or ""))
        for row in snapshot.normalized.file_activities
    )
    for warning in snapshot.normalized.parse_warnings:
        candidates = (
            tuple(
                event
                for event in snapshot.indexes.raw_events_by_record_index.get(
                    warning.record_index, ()
                )
                if event.source_id == warning.source_id
            )
            if warning.record_index is not None
            else ()
        )
        rows.append(
            SequenceActivity(
                "parse_warning",
                candidates[0].record_index if len(candidates) == 1 else None,
            )
        )
    return rows


def sequence_bins(
    activities: list[SequenceActivity],
    first_record_index: int | None,
    last_record_index: int | None,
) -> list[SequenceBin]:
    if first_record_index is None or last_record_index is None:
        return []
    span = last_record_index - first_record_index + 1
    bin_count = min(span, MAX_SEQUENCE_BINS)
    counts_by_bin = [Counter[ActivityCategory]() for _ in range(bin_count)]
    for activity in activities:
        if activity.record_index is None:
            continue
        index = min(
            ((activity.record_index - first_record_index) * bin_count) // span,
            bin_count - 1,
        )
        counts_by_bin[index][activity.category] += 1
    return [
        SequenceBin(
            index=index,
            first_record_index=first_record_index + (index * span) // bin_count,
            last_record_index=(first_record_index + ((index + 1) * span) // bin_count - 1),
            counts=activity_counts(counts),
        )
        for index, counts in enumerate(counts_by_bin)
    ]


def activity_counts(counts: Counter[ActivityCategory]) -> SequenceActivityCounts:
    return SequenceActivityCounts(
        **{category: counts[category] for category in ACTIVITY_CATEGORIES}
    )


def evidence_marker_rows(
    snapshot: DiagnosticSnapshot,
    *,
    scores: list[ReportScore],
    classifications: list[ReportClassification],
    evidence: dict[str, list[EvidenceItem]],
    ending: ReportEnding,
) -> tuple[list[SequenceEvidenceMarker], Counter[str]]:
    references = list(marker_references(scores, classifications, evidence, ending))
    if snapshot.analysis.compatibility is AnalysisCompatibility.CURRENT:
        references.extend(file_loop_marker_references(snapshot))
    markers: list[SequenceEvidenceMarker] = []
    unresolved: Counter[str] = Counter()
    seen: set[tuple[str, str, str | None]] = set()
    for reference in references:
        key = (reference.category, reference.evidence_id, reference.source_event_id)
        if key in seen:
            continue
        seen.add(key)
        event = snapshot.indexes.raw_events_by_id.get(reference.source_event_id or "")
        if event is None:
            unresolved[reference.category] += 1
            continue
        markers.append(
            SequenceEvidenceMarker(
                category=reference.category,
                evidence_id=reference.evidence_id,
                source_event_id=event.event_id,
                record_index=event.record_index,
                observed_at=event.timestamp,
            )
        )
    markers.sort(
        key=lambda row: (
            row.record_index,
            row.category,
            row.evidence_id,
            row.source_event_id,
        )
    )
    return markers, unresolved


def marker_references(
    scores: list[ReportScore],
    classifications: list[ReportClassification],
    evidence: dict[str, list[EvidenceItem]],
    ending: ReportEnding,
) -> Iterable[MarkerReference]:
    for score in scores:
        for event_id in (*score.source_event_ids, *score.unresolved_source_event_ids):
            yield MarkerReference("score", score.name, event_id)
    for classification in classifications:
        for event_id in (
            *classification.source_event_ids,
            *classification.unresolved_source_event_ids,
        ):
            yield MarkerReference("classification", classification.classification_id, event_id)
    for section_name, items in evidence.items():
        for item in items:
            for event_id in item_source_event_ids(item):
                yield MarkerReference(section_name, item.evidence_id, event_id)
    for event_id in (*ending.source_event_ids, *ending.unresolved_source_event_ids):
        yield MarkerReference("ending", f"ending:{event_id}", event_id)


def item_source_event_ids(item: EvidenceItem) -> tuple[str | None, ...]:
    if isinstance(item, MessageSignalEvidence):
        matched = (item.matched_source_event_id,) if item.matched_message_id is not None else ()
        return (item.source_event_id, *matched)
    if isinstance(item, (CommandFailureEvidence, ToolFailureEvidence)):
        return (item.source_event_id,)
    if isinstance(item, (FailureGroupEvidence, FileLoopEvidence)):
        return tuple((*item.source_event_ids, *item.unresolved_source_event_ids))
    if isinstance(item, ClassificationReferenceEvidence):
        return (item.source_event_id,)
    return ()


def file_loop_marker_references(snapshot: DiagnosticSnapshot) -> list[MarkerReference]:
    feature = next(
        (
            row
            for row in snapshot.analysis.session_features
            if row.feature_name == "same_file_edited_repeatedly_count"
        ),
        None,
    )
    if feature is None:
        return []
    paths = feature.evidence.get("paths")
    event_map = feature.evidence.get("source_event_ids_by_path")
    if not isinstance(paths, list) or not isinstance(event_map, dict):
        return []
    references: list[MarkerReference] = []
    for path in paths:
        if not isinstance(path, str):
            continue
        event_ids = event_map.get(path)
        if not isinstance(event_ids, list):
            continue
        evidence_id = stable_id("report-file-loop", path)
        references.extend(
            MarkerReference("repeated_file_edits", evidence_id, event_id)
            for event_id in event_ids
            if isinstance(event_id, str)
        )
    return references

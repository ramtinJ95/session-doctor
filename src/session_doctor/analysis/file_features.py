from __future__ import annotations

from collections import Counter, defaultdict

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import FileActivity, SessionFeature

from .feature_factories import session_feature
from .feature_models import SessionFeatureContext

MUTATING_FILE_OPERATIONS = frozenset(
    {"create", "delete", "edit", "move", "patch", "rename", "update", "write"}
)


def file_activity_identity(activity: FileActivity) -> str:
    if activity.canonical_path is not None:
        return activity.canonical_path
    return activity.normalized_path


def file_activity_session_features(
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "edited_file_count",
            len(context.file_edit_counts),
            evidence={
                "paths": sorted(context.file_edit_counts),
                "source_event_ids_by_path": context.file_edit_events,
                "source_event_ids": sorted(
                    {
                        source_event_id
                        for source_event_ids in context.file_edit_events.values()
                        for source_event_id in source_event_ids
                    }
                ),
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "same_file_edited_repeatedly_count",
            len(context.repeated_file_edits),
            evidence={
                "paths": context.repeated_file_edits,
                "source_event_ids_by_path": context.repeated_file_edit_events,
                "source_event_ids": sorted(
                    {
                        source_event_id
                        for source_event_ids in context.repeated_file_edit_events.values()
                        for source_event_id in source_event_ids
                    }
                ),
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "max_edits_to_single_file",
            max(context.file_edit_counts.values(), default=0),
            evidence=max_file_edit_evidence(context),
        ),
    ]


def max_file_edit_evidence(context: SessionFeatureContext) -> dict[str, object]:
    max_edit_count = max(context.file_edit_counts.values(), default=0)
    if max_edit_count == 0:
        return {"paths": [], "source_event_ids_by_path": {}, "source_event_ids": []}
    max_edit_paths = sorted(
        path for path, count in context.file_edit_counts.items() if count == max_edit_count
    )
    source_event_ids_by_path = {
        path: context.file_edit_events.get(path, []) for path in max_edit_paths
    }
    return {
        "paths": max_edit_paths,
        "source_event_ids_by_path": source_event_ids_by_path,
        "source_event_ids": sorted(
            {
                source_event_id
                for source_event_ids in source_event_ids_by_path.values()
                for source_event_id in source_event_ids
            }
        ),
    }


def file_edit_source_events(bundle: ParsedSessionBundle) -> dict[str, list[str]]:
    source_events_by_path: defaultdict[str, set[str]] = defaultdict(set)
    for activity in bundle.file_activities:
        if activity.operation not in MUTATING_FILE_OPERATIONS:
            continue
        if activity.source_event_id:
            source_events_by_path[file_activity_identity(activity)].add(activity.source_event_id)
    return {
        path: sorted(source_event_ids)
        for path, source_event_ids in sorted(source_events_by_path.items())
    }


def repeated_file_edit_source_events(
    file_edit_events: dict[str, list[str]],
    file_edit_counts: Counter[str],
) -> dict[str, list[str]]:
    return {
        path: file_edit_events.get(path, [])
        for path, count in file_edit_counts.items()
        if count > 1
    }

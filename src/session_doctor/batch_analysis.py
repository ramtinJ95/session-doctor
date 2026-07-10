from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .analysis import ANALYZER_VERSION
from .analysis_workflow import (
    AnalysisFailureCode,
    AnalysisWorkflowError,
    SessionAnalysisError,
    analyze_session,
)
from .privacy import redact_home
from .store import AnalysisCompatibility, DuckDBStore, SessionScopeFilters


@dataclass(frozen=True)
class BatchAnalysisFailure:
    session_id: str
    code: AnalysisFailureCode
    message: str
    cause: Exception | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class BatchAnalysisResult:
    filters: SessionScopeFilters
    force: bool
    write_artifacts: bool
    matching_count: int
    succeeded_session_ids: tuple[str, ...]
    skipped_session_ids: tuple[str, ...]
    failures: tuple[BatchAnalysisFailure, ...]

    @property
    def selected_count(self) -> int:
        return len(self.succeeded_session_ids) + len(self.failures)


def analyze_all_sessions(
    store: DuckDBStore,
    database_path: Path,
    filters: SessionScopeFilters,
    *,
    force: bool,
    write_artifacts: bool,
) -> BatchAnalysisResult:
    targets = store.list_analysis_targets(filters)
    skipped_session_ids = tuple(
        target.session_id
        for target in targets
        if not force and target.compatibility is AnalysisCompatibility.CURRENT
    )
    selected_targets = tuple(
        target
        for target in targets
        if force or target.compatibility is not AnalysisCompatibility.CURRENT
    )
    succeeded_session_ids: list[str] = []
    failures: list[BatchAnalysisFailure] = []

    for target in selected_targets:
        try:
            analyze_session(
                store,
                target.session_id,
                database_path,
                artifact=None,
                no_artifact=not write_artifacts,
            )
        except AnalysisWorkflowError as exc:
            failures.append(
                BatchAnalysisFailure(
                    session_id=target.session_id,
                    code=exc.code,
                    message=exc.safe_message,
                    cause=exc,
                )
            )
        except Exception as exc:
            failure = SessionAnalysisError()
            failures.append(
                BatchAnalysisFailure(
                    session_id=target.session_id,
                    code=failure.code,
                    message=failure.safe_message,
                    cause=exc,
                )
            )
        else:
            succeeded_session_ids.append(target.session_id)

    return BatchAnalysisResult(
        filters=filters,
        force=force,
        write_artifacts=write_artifacts,
        matching_count=len(targets),
        succeeded_session_ids=tuple(succeeded_session_ids),
        skipped_session_ids=skipped_session_ids,
        failures=tuple(failures),
    )


def batch_analysis_payload(result: BatchAnalysisResult) -> dict[str, object]:
    return {
        "filters": {
            "project": (
                redact_home(result.filters.project_path)
                if result.filters.project_path is not None
                else None
            ),
            "agent": result.filters.agent_name,
        },
        "analyzer_version": ANALYZER_VERSION,
        "force": result.force,
        "write_artifacts": result.write_artifacts,
        "counts": {
            "matching": result.matching_count,
            "selected": result.selected_count,
            "succeeded": len(result.succeeded_session_ids),
            "skipped": len(result.skipped_session_ids),
            "failed": len(result.failures),
        },
        "succeeded_session_ids": list(result.succeeded_session_ids),
        "skipped_session_ids": list(result.skipped_session_ids),
        "failures": [
            {
                "session_id": failure.session_id,
                "code": failure.code.value,
                "message": failure.message,
            }
            for failure in result.failures
        ],
    }

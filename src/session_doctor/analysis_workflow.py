from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .analysis import ANALYZER_VERSION, analyze_features, classify_session
from .artifacts import (
    ArtifactWriteError,
    analysis_payload,
    artifact_path_for_analysis,
    write_analysis_artifact,
)
from .ids import stable_id
from .schemas import AnalysisRun, SessionClassification, SessionFeature
from .store import DuckDBStore


@dataclass
class AnalysisResult:
    analysis_run: AnalysisRun
    session_features: list[SessionFeature]
    classifications: list[SessionClassification]
    payload: dict[str, object]


class AnalysisFailureCode(StrEnum):
    SESSION_NOT_LOADABLE = "session_not_loadable"
    ANALYSIS_FAILED = "analysis_failed"
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"
    PERSISTENCE_FAILED = "persistence_failed"


class AnalysisWorkflowError(RuntimeError):
    code: AnalysisFailureCode
    safe_message: str


class SessionNotLoadableError(AnalysisWorkflowError):
    code = AnalysisFailureCode.SESSION_NOT_LOADABLE
    safe_message = "Session could not be loaded"

    def __init__(self, *, not_found: bool = False) -> None:
        self.not_found = not_found
        super().__init__(self.safe_message)


class SessionAnalysisError(AnalysisWorkflowError):
    code = AnalysisFailureCode.ANALYSIS_FAILED
    safe_message = "Session analysis failed"


class AnalysisArtifactError(AnalysisWorkflowError):
    code = AnalysisFailureCode.ARTIFACT_WRITE_FAILED
    safe_message = "Analysis artifact could not be written"

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(self.safe_message)


class AnalysisPersistenceError(AnalysisWorkflowError):
    code = AnalysisFailureCode.PERSISTENCE_FAILED
    safe_message = "Analysis results could not be persisted"


def analyze_session(
    store: DuckDBStore,
    session_id: str,
    database_path: Path,
    artifact: Path | None,
    no_artifact: bool,
) -> AnalysisResult:
    try:
        bundle = store.load_session_bundle(session_id)
    except Exception as exc:
        raise SessionNotLoadableError from exc
    if bundle is None or bundle.session is None:
        raise SessionNotLoadableError(not_found=True)

    started_at = datetime.now(UTC)
    analysis_run_id = stable_id("analysis_run", session_id, started_at.isoformat())
    try:
        extracted_features = analyze_features(bundle, analysis_run_id)
        classifications = classify_session(
            bundle,
            analysis_run_id,
            extracted_features.message_features,
            extracted_features.session_features,
        )
    except Exception as exc:
        raise SessionAnalysisError from exc
    artifact_path = artifact_path_for_analysis(database_path, session_id, artifact, no_artifact)
    analysis_run = AnalysisRun(
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        analyzer_version=ANALYZER_VERSION,
        artifact_path=str(artifact_path) if artifact_path else None,
    )
    payload = analysis_payload(
        bundle.session,
        analysis_run,
        extracted_features.message_features,
        extracted_features.session_features,
        classifications,
    )

    staged_artifact_path = None
    if artifact_path is not None:
        if artifact_path.is_dir():
            raise AnalysisArtifactError(artifact_path)
        staged_artifact_path = artifact_path.with_name(
            f".{artifact_path.name}.{analysis_run_id}.tmp"
        )
        try:
            write_analysis_artifact(staged_artifact_path, payload)
        except ArtifactWriteError as exc:
            raise AnalysisArtifactError(artifact_path) from exc

    try:
        store.replace_analysis_rows(
            analysis_run,
            extracted_features.message_features,
            extracted_features.session_features,
            classifications,
        )
    except Exception as exc:
        discard_staged_artifact(staged_artifact_path)
        raise AnalysisPersistenceError from exc

    if staged_artifact_path is not None and artifact_path is not None:
        try:
            staged_artifact_path.replace(artifact_path)
        except OSError as exc:
            discard_staged_artifact(staged_artifact_path)
            analysis_run = analysis_run.model_copy(update={"artifact_path": None})
            try:
                store.replace_analysis_rows(
                    analysis_run,
                    extracted_features.message_features,
                    extracted_features.session_features,
                    classifications,
                )
            except Exception as persistence_exc:
                raise AnalysisPersistenceError from persistence_exc
            raise AnalysisArtifactError(artifact_path) from exc

    return AnalysisResult(
        analysis_run=analysis_run,
        session_features=extracted_features.session_features,
        classifications=classifications,
        payload=payload,
    )


def discard_staged_artifact(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass

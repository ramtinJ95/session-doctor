from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from .analysis import analyze_features, classify_session
from .artifacts import analysis_payload, artifact_path_for_analysis, write_analysis_artifact
from .ids import stable_id
from .schemas import AnalysisRun, SessionClassification, SessionFeature
from .store import DuckDBStore


@dataclass
class AnalysisResult:
    analysis_run: AnalysisRun
    session_features: list[SessionFeature]
    classifications: list[SessionClassification]
    payload: dict[str, object]


def analyze_session(
    store: DuckDBStore,
    session_id: str,
    database_path: Path,
    artifact: Path | None,
    no_artifact: bool,
    console: Console,
) -> AnalysisResult:
    bundle = store.load_session_bundle(session_id)
    if bundle is None or bundle.session is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)

    started_at = datetime.now(UTC)
    analysis_run_id = stable_id("analysis_run", session_id, started_at.isoformat())
    extracted_features = analyze_features(bundle, analysis_run_id)
    classifications = classify_session(
        bundle,
        analysis_run_id,
        extracted_features.message_features,
        extracted_features.session_features,
    )
    artifact_path = artifact_path_for_analysis(database_path, session_id, artifact, no_artifact)
    analysis_run = AnalysisRun(
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        analyzer_version="phase6",
        artifact_path=str(artifact_path) if artifact_path else None,
    )
    payload = analysis_payload(
        bundle.session,
        analysis_run,
        extracted_features.message_features,
        extracted_features.session_features,
        classifications,
    )

    if artifact_path:
        write_analysis_artifact(artifact_path, payload)

    store.replace_analysis_rows(
        analysis_run,
        extracted_features.message_features,
        extracted_features.session_features,
        classifications,
    )

    return AnalysisResult(
        analysis_run=analysis_run,
        session_features=extracted_features.session_features,
        classifications=classifications,
        payload=payload,
    )

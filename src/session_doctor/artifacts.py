from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from .schemas import (
    AnalysisRun,
    MessageFeature,
    Session,
    SessionClassification,
    SessionFeature,
)

console = Console()


def artifact_path_for_analysis(
    database_path: Path,
    session_id: str,
    artifact: Path | None,
    no_artifact: bool,
) -> Path | None:
    if no_artifact:
        return None
    if artifact is not None:
        return artifact.expanduser()
    return database_path.parent / "artifacts" / f"{session_id}-analysis.json"


def write_analysis_artifact(path: Path, payload: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        console.print(f"[red]Could not write artifact:[/red] {path} ({exc})")
        raise typer.Exit(1) from exc


def analysis_payload(
    session: Session,
    analysis_run: AnalysisRun,
    message_features: list[MessageFeature],
    session_features: list[SessionFeature],
    classifications: list[SessionClassification],
) -> dict[str, object]:
    return {
        "session": session.model_dump(mode="json"),
        "analysis_run": analysis_run.model_dump(mode="json"),
        "summary_metrics": {
            feature.feature_name: feature.feature_value for feature in session_features
        },
        "message_features": [feature.model_dump(mode="json") for feature in message_features],
        "session_features": [feature.model_dump(mode="json") for feature in session_features],
        "classifications": [
            classification.model_dump(mode="json") for classification in classifications
        ],
    }

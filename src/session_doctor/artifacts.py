from __future__ import annotations

import json
from pathlib import Path

from .schemas import (
    AnalysisRun,
    MessageFeature,
    Session,
    SessionClassification,
    SessionFeature,
)


class ArtifactWriteError(RuntimeError):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__("analysis artifact could not be written")


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
        raise ArtifactWriteError(path) from exc


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

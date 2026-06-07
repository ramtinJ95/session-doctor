from __future__ import annotations

from session_doctor.ids import stable_id
from session_doctor.schemas import SessionClassification


def classification_metadata(
    *,
    rule: str,
    contributing_features: list[str],
    score_feature: str | None = None,
    threshold: float | int | None = None,
    extra_thresholds: dict[str, float | int] | None = None,
    fixed_score: float | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "rule": rule,
        "contributing_features": contributing_features,
    }
    if score_feature is not None:
        metadata["score_feature"] = score_feature
    if threshold is not None:
        metadata["threshold"] = threshold
    if extra_thresholds:
        metadata["extra_thresholds"] = extra_thresholds
    if fixed_score is not None:
        metadata["fixed_score"] = fixed_score
    return metadata


def classification(
    *,
    analysis_run_id: str,
    session_id: str,
    label: str,
    score: float,
    confidence: float,
    evidence_event_ids: list[str],
    evidence_summary: str,
    metadata: dict[str, object],
) -> SessionClassification:
    return SessionClassification(
        session_classification_id=stable_id(
            "session_classification",
            analysis_run_id,
            session_id,
            label,
        ),
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        label=label,
        score=score,
        confidence=confidence,
        evidence_event_ids=evidence_event_ids,
        evidence_summary=evidence_summary,
        metadata=metadata,
    )

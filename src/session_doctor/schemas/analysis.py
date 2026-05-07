from __future__ import annotations

from pydantic import Field

from .common import Confidence, Metadata, OptionalDatetime, SessionDoctorModel


class AnalysisRun(SessionDoctorModel):
    analysis_run_id: str
    session_id: str
    started_at: OptionalDatetime = None
    completed_at: OptionalDatetime = None
    analyzer_version: str
    artifact_path: str | None = None
    metadata: Metadata = Field(default_factory=dict)


class MessageFeature(SessionDoctorModel):
    message_feature_id: str
    analysis_run_id: str
    session_id: str
    message_id: str
    source_event_id: str | None = None
    feature_name: str
    feature_value: str
    score: Confidence = 1.0
    evidence: Metadata = Field(default_factory=dict)
    metadata: Metadata = Field(default_factory=dict)


class SessionFeature(SessionDoctorModel):
    session_feature_id: str
    analysis_run_id: str
    session_id: str
    feature_name: str
    feature_value: str
    score: Confidence = 1.0
    evidence: Metadata = Field(default_factory=dict)
    metadata: Metadata = Field(default_factory=dict)


class SessionClassification(SessionDoctorModel):
    session_classification_id: str
    analysis_run_id: str
    session_id: str
    label: str
    score: Confidence
    confidence: Confidence
    evidence_event_ids: list[str] = Field(default_factory=list)
    evidence_summary: str
    metadata: Metadata = Field(default_factory=dict)

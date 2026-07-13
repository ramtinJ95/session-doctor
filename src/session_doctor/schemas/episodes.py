from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .common import SessionDoctorModel


class BoundaryDecision(StrEnum):
    SPLIT = "split"
    NO_SPLIT = "no_split"
    AMBIGUOUS = "ambiguous"


class BoundaryReason(StrEnum):
    EXPLICIT_NEW_TASK = "explicit_new_task"
    CORRECTION_OR_REPEAT = "correction_or_repeat"
    CLOSURE_AND_TOPIC_SHIFT = "closure_and_topic_shift"
    WEAK_OR_CONFLICTING = "weak_or_conflicting"


class EpisodeBoundary(SessionDoctorModel):
    boundary_id: str
    segmentation_version: str
    session_id: str
    left_user_anchor_id: str
    right_user_anchor_id: str
    decision: BoundaryDecision
    reason: BoundaryReason
    evidence_anchor_ids: list[str] = Field(min_length=2)
    broad_goal_similarity: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_anchors(self) -> EpisodeBoundary:
        if self.left_user_anchor_id == self.right_user_anchor_id:
            raise ValueError("boundary anchors must be distinct")
        if not {self.left_user_anchor_id, self.right_user_anchor_id}.issubset(
            self.evidence_anchor_ids
        ):
            raise ValueError("boundary anchors must be cited as evidence")
        return self


class TaskEpisode(SessionDoctorModel):
    episode_id: str
    segmentation_version: str
    session_id: str
    first_user_anchor_id: str
    last_user_anchor_id: str
    user_anchor_ids: list[str] = Field(min_length=1)
    event_anchor_ids: list[str] = Field(min_length=1)
    boundary_ids: list[str] = Field(default_factory=list)
    lifecycle_state: str
    provisional: bool


class EpisodeObservation(SessionDoctorModel):
    observation_id: str
    episode_id: str
    observation_kind: str
    evidence_anchor_ids: list[str] = Field(min_length=1)


class EpisodeAnalysis(SessionDoctorModel):
    segmentation_version: str
    session_id: str
    lifecycle_observation_id: str
    lifecycle_state: str
    episodes: list[TaskEpisode] = Field(default_factory=list)
    boundaries: list[EpisodeBoundary] = Field(default_factory=list)
    observations: list[EpisodeObservation] = Field(default_factory=list)

from __future__ import annotations

from enum import StrEnum
from typing import Literal

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


class AnalysisAnchor(SessionDoctorModel):
    anchor_id: str
    anchor_kind: str
    entity_id: str
    payload_digest: str


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


class EpisodeExactInput(SessionDoctorModel):
    analysis_identity: str
    discovery_role: Literal["requested", "ancestor", "descendant", "candidate"]
    session_id: str
    normalization_run_id: str
    snapshot_bundle_id: str
    lifecycle_observation_id: str


class EpisodeMembership(SessionDoctorModel):
    source_analysis_identity: str
    entity_kind: str
    entity_id: str
    normalization_run_id: str
    entity_order: int = Field(ge=0)
    membership_status: Literal["assigned", "ambiguous", "unassigned"]
    source_episode_id: str | None = None
    rollup_owner_status: Literal["known", "unavailable"]
    rollup_owner_analysis_identity: str | None = None
    rollup_owner_episode_id: str | None = None
    aggregate_eligibility: Literal["direct", "excluded_delegated", "ineligible"]
    reason: str
    candidate_episode_keys: list[tuple[str, str]] = Field(default_factory=list)


class EpisodeTopologyCandidate(SessionDoctorModel):
    topology_candidate_id: str
    direction: Literal["parent", "child"]
    native_spawn_identity: str | None = None
    parent_analysis_identity: str | None = None
    child_analysis_identity: str | None = None
    status: Literal["linked", "unavailable", "ambiguous", "not_child"]
    reason: str
    endpoint_status: Literal["observed", "missing", "unavailable"]
    witness_bundle_ids: list[str] = Field(default_factory=list)


class EpisodeDelegationBinding(SessionDoctorModel):
    child_analysis_identity: str
    parent_analysis_identity: str
    parent_episode_id: str
    spawn_entity_kind: str
    spawn_entity_id: str
    spawn_anchor_id: str
    witness_bundle_ids: list[str]


class EpisodeDelegationEdge(SessionDoctorModel):
    delegation_id: str
    child_analysis_identity: str
    child_episode_id: str
    parent_analysis_identity: str
    parent_episode_id: str


class EpisodeDelegation(SessionDoctorModel):
    candidates: list[EpisodeTopologyCandidate] = Field(default_factory=list)
    bindings: list[EpisodeDelegationBinding] = Field(default_factory=list)
    child_episode_edges: list[EpisodeDelegationEdge] = Field(default_factory=list)


class EpisodeAnalysisPayload(SessionDoctorModel):
    schema_version: Literal["episode-analysis-v2"] = "episode-analysis-v2"
    requested_session_id: str
    analysis_identity: str
    episode_projection_id: str
    exact_inputs: list[EpisodeExactInput]
    episodes: list[TaskEpisode]
    boundaries: list[EpisodeBoundary]
    observations: list[EpisodeObservation]
    memberships: list[EpisodeMembership]
    delegation: EpisodeDelegation

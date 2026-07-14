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


class EpisodeKind(StrEnum):
    DIRECT = "direct"
    DELEGATED = "delegated"


class EpisodeAggregateEligibility(StrEnum):
    ELIGIBLE_DIRECT = "eligible_direct"
    INELIGIBLE_DELEGATED_CHILD = "ineligible_delegated_child"


class EpisodeMembershipStatus(StrEnum):
    ASSIGNED = "assigned"
    AMBIGUOUS = "ambiguous"
    UNASSIGNED = "unassigned"


class DelegationStatus(StrEnum):
    LINKED = "linked"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


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
    first_user_anchor_id: str | None = None
    last_user_anchor_id: str | None = None
    user_anchor_ids: list[str] = Field(default_factory=list)
    event_anchor_ids: list[str] = Field(min_length=1)
    boundary_ids: list[str] = Field(default_factory=list)
    lifecycle_state: str
    provisional: bool
    episode_kind: EpisodeKind = EpisodeKind.DIRECT
    parent_episode_id: str | None = None
    rollup_owner_episode_id: str | None = None
    aggregate_eligibility: EpisodeAggregateEligibility = EpisodeAggregateEligibility.ELIGIBLE_DIRECT


class EpisodeObservation(SessionDoctorModel):
    observation_id: str
    episode_id: str
    observation_kind: str
    evidence_anchor_ids: list[str] = Field(min_length=1)


class EpisodeEntityMembership(SessionDoctorModel):
    membership_id: str
    analysis_identity: str
    normalization_run_id: str
    entity_kind: str
    entity_id: str
    status: EpisodeMembershipStatus
    reason: str
    source_episode_id: str | None = None
    rollup_owner_episode_id: str | None = None
    candidate_episode_ids: list[str] = Field(default_factory=list)
    evidence_anchor_ids: list[str] = Field(default_factory=list)
    additive_aggregate_eligible: bool


class EpisodeDelegation(SessionDoctorModel):
    delegation_id: str
    topology_version: str
    status: DelegationStatus
    child_analysis_identity: str
    child_episode_id: str
    child_session_id: str
    parent_analysis_identity: str | None = None
    parent_episode_id: str | None = None
    parent_session_id: str | None = None
    rollup_owner_episode_id: str
    spawn_tool_call_id: str | None = None
    spawn_event_id: str | None = None
    parent_candidate_episode_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, object] = Field(default_factory=dict)


class EpisodeUnavailableChild(SessionDoctorModel):
    child_session_id: str
    reason: str
    snapshot_id: str | None = None
    logical_source_id: str | None = None


class EpisodeTopologyProjection(SessionDoctorModel):
    topology_projection_id: str
    topology_version: str
    analysis_identity: str
    delegations: list[EpisodeDelegation] = Field(default_factory=list)
    unavailable_children: list[EpisodeUnavailableChild] = Field(default_factory=list)


class EpisodeAnalysis(SessionDoctorModel):
    schema_version: str = "episode-analysis-v2"
    analysis_identity: str
    normalization_run_id: str
    segmentation_version: str
    session_id: str
    lifecycle_observation_id: str
    lifecycle_state: str
    episodes: list[TaskEpisode] = Field(default_factory=list)
    boundaries: list[EpisodeBoundary] = Field(default_factory=list)
    observations: list[EpisodeObservation] = Field(default_factory=list)
    entity_memberships: list[EpisodeEntityMembership] = Field(default_factory=list)
    delegations: list[EpisodeDelegation] = Field(default_factory=list)
    topology_projection: EpisodeTopologyProjection | None = None

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from session_doctor.schemas import SessionDoctorModel

EVALUATION_SCHEMA_VERSION = "evaluation-schema-v1"
ANNOTATION_PROTOCOL_VERSION = "annotation-protocol-v1"


class PacketKind(StrEnum):
    BOUNDARY = "boundary"
    EPISODE = "episode"


class SourceFamilyStatus(StrEnum):
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    ESTABLISHED = "established"


class IdentityExposureStatus(StrEnum):
    BLIND_ELIGIBLE = "blind_eligible"
    IDENTITY_EXPOSED = "identity_exposed"
    TARGET_IDENTITY_UNVERIFIABLE = "target_identity_unverifiable"


class JudgeConsensusStatus(StrEnum):
    UNANIMOUS = "unanimous"
    DISPUTED = "disputed"
    INSUFFICIENT = "insufficient"


class AuditSelectionStatus(StrEnum):
    NOT_SELECTED = "not_selected"
    SELECTED = "selected"


class AuditEligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


class HumanReviewKind(StrEnum):
    PANEL_DISPUTE = "panel_dispute"
    PANEL_INSUFFICIENT = "panel_insufficient"
    CONSENSUS_AUDIT = "consensus_audit"


class ReferenceResolutionStatus(StrEnum):
    JUDGE_CONSENSUS = "judge_consensus"
    HUMAN_RESOLVED = "human_resolved"
    AMBIGUOUS = "ambiguous"


class RoutingEnvelope(SessionDoctorModel):
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    packet_kind: PacketKind
    evaluation_corpus_id: str
    normalization_run_id: str | None
    snapshot_bundle_id: str
    source_family_id: str | None = None
    source_family_status: SourceFamilyStatus = SourceFamilyStatus.UNKNOWN
    family_policy_version: str | None = None
    target_model_identities: list[str] = Field(default_factory=list)
    excluded_judge_identities: list[str] = Field(default_factory=list)
    identity_exposure_status: IdentityExposureStatus
    judge_packet_hash: str

    @model_validator(mode="after")
    def validate_family(self) -> RoutingEnvelope:
        if self.source_family_status is SourceFamilyStatus.ESTABLISHED and (
            self.source_family_id is None or self.family_policy_version is None
        ):
            raise ValueError("established family requires identity and policy version")
        if self.source_family_status is not SourceFamilyStatus.ESTABLISHED and (
            self.source_family_id is not None or self.family_policy_version is not None
        ):
            raise ValueError("pre-seal family identity must remain unset")
        return self


class PacketEvent(SessionDoctorModel):
    evidence_id: str
    source_event_id: str | None = None
    entity_kind: str
    role: str | None = None
    text: str | None = None
    text_hash: str | None = None
    text_length: int | None = None
    structure: dict[str, object] = Field(default_factory=dict)


class BoundaryPacket(SessionDoctorModel):
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    packet_kind: Literal[PacketKind.BOUNDARY] = PacketKind.BOUNDARY
    left_user_event_id: str
    right_user_event_id: str
    adjacent_user_turns: list[PacketEvent]
    intervening_normalized_events: list[PacketEvent] = Field(default_factory=list)
    bounded_context_events: list[PacketEvent] = Field(default_factory=list)
    anonymized_capability_support: list[dict[str, str]] = Field(default_factory=list)
    allowed_answers: list[str] = Field(default_factory=lambda: ["ambiguous", "no_split", "split"])

    @model_validator(mode="after")
    def validate_boundary_rubric(self) -> BoundaryPacket:
        if self.allowed_answers != ["ambiguous", "no_split", "split"]:
            raise ValueError("boundary packet answers are fixed by protocol")
        if len(self.adjacent_user_turns) != 2 or any(
            event.role != "user" for event in self.adjacent_user_turns
        ):
            raise ValueError("boundary packet requires exactly two adjacent user turns")
        expected_anchors = [
            event.source_event_id or event.evidence_id for event in self.adjacent_user_turns
        ]
        if [self.left_user_event_id, self.right_user_event_id] != expected_anchors:
            raise ValueError("boundary anchors must match adjacent user turns")
        event_ids = [
            event.evidence_id
            for event in (
                self.adjacent_user_turns
                + self.intervening_normalized_events
                + self.bounded_context_events
            )
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("boundary packet evidence IDs must be unique")
        return self


class EpisodePacket(SessionDoctorModel):
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    packet_kind: Literal[PacketKind.EPISODE] = PacketKind.EPISODE
    episode_anchor_ids: list[str]
    annotation_task: str
    normalized_episode_events: list[PacketEvent]
    bounded_context_events: list[PacketEvent] = Field(default_factory=list)
    raw_capture_observations: list[dict[str, object]] = Field(default_factory=list)
    anonymized_model_roles: dict[str, str] = Field(default_factory=dict)
    anonymized_capability_support: list[dict[str, str]] = Field(default_factory=list)
    allowed_answers: list[str]


class EvaluationPacketExport(SessionDoctorModel):
    routing: RoutingEnvelope
    judge_packet: BoundaryPacket | EpisodePacket


class JudgeAnnotation(SessionDoctorModel):
    judge_annotation_id: str
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    judge_model: str
    judge_provider: str
    judge_prompt_version: str
    answer: str
    evidence_ids: list[str]
    rationale: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_judge_provenance(self) -> JudgeAnnotation:
        if not self.evidence_ids:
            raise ValueError("judge annotation requires evidence")
        if any(
            not value.strip()
            for value in (
                self.judge_model,
                self.judge_provider,
                self.judge_prompt_version,
                self.rationale,
            )
        ):
            raise ValueError("judge identity, prompt, and rationale must be nonblank")
        return self


class JudgePanelResolution(SessionDoctorModel):
    judge_panel_resolution_id: str
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    judge_annotation_ids: list[str]
    consensus_status: JudgeConsensusStatus
    unanimous_answer: str | None = None
    resolved_at: datetime


class AuditSelection(SessionDoctorModel):
    audit_selection_id: str
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    judge_panel_resolution_id: str
    eligibility_status: AuditEligibilityStatus
    selection_status: AuditSelectionStatus
    selection_seed_id: str
    selection_reason: str
    selected_at: datetime


class AuditProtocol(SessionDoctorModel):
    audit_protocol_id: str
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    evaluation_corpus_id: str
    expected_packet_count: int = Field(gt=0)
    selection_seed_id: str
    cohort_packet_ids: list[str]
    eligible_packet_ids: list[str]
    selected_packet_ids: list[str]
    frozen_at: datetime


class HumanAdjudication(SessionDoctorModel):
    human_adjudication_id: str
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    judge_panel_resolution_id: str
    audit_selection_id: str | None = None
    review_kind: HumanReviewKind
    reviewer_identity: str
    answer: str
    evidence_ids: list[str]
    rationale: str
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_human_provenance(self) -> HumanAdjudication:
        if not self.evidence_ids:
            raise ValueError("human adjudication requires evidence")
        if not self.reviewer_identity.strip() or not self.rationale.strip():
            raise ValueError("human reviewer identity and rationale must be nonblank")
        return self


class ReferenceResolution(SessionDoctorModel):
    reference_resolution_id: str
    schema_version: Literal["evaluation-schema-v1"] = EVALUATION_SCHEMA_VERSION
    annotation_protocol_version: Literal["annotation-protocol-v1"] = ANNOTATION_PROTOCOL_VERSION
    packet_id: str
    resolution_status: ReferenceResolutionStatus
    answer: str
    source_judge_panel_resolution_id: str
    source_audit_selection_id: str | None = None
    source_human_adjudication_id: str | None = None
    resolved_at: datetime

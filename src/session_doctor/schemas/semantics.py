from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .common import SessionDoctorModel


class CapabilitySupport(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class InstrumentationStatus(StrEnum):
    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"


class AdapterCapabilityDeclaration(SessionDoctorModel):
    capability: str
    support: CapabilitySupport
    instrumentation: str


class CapabilityEvidence(SessionDoctorModel):
    capability: str
    support: CapabilitySupport
    instrumentation: str
    evidence_status: InstrumentationStatus
    evidence_ids: list[str] = Field(default_factory=list)


class SourceOrderItem(SessionDoctorModel):
    event_id: str
    source_id: str
    record_index: int


class CausalOrderEdge(SessionDoctorModel):
    parent_event_id: str
    child_event_id: str
    relation: str = "native_parent"


class OrderingProjection(SessionDoctorModel):
    ordering_version: str
    source_order: list[SourceOrderItem] = Field(default_factory=list)
    causal_edges: list[CausalOrderEdge] = Field(default_factory=list)
    ambiguous_native_event_ids: list[str] = Field(default_factory=list)
    unresolved_parent_event_ids: list[str] = Field(default_factory=list)
    cross_source_order: str = "partial_order"


class ProjectIdentityState(StrEnum):
    NATIVE_REPOSITORY = "native_repository"
    OBSERVED_VCS_ROOT = "observed_vcs_root"
    STORED_CWD = "stored_cwd"
    UNKNOWN = "unknown"


class ProjectIdentity(SessionDoctorModel):
    state: ProjectIdentityState
    path: str | None = None
    evidence: str


class ModelIdentityState(StrEnum):
    ONE_MODEL = "one_model"
    MIXED_MODELS = "mixed_models"
    UNKNOWN = "unknown"


class ModelIdentity(SessionDoctorModel):
    state: ModelIdentityState
    models: list[str] = Field(default_factory=list)


class UsageSemantics(StrEnum):
    CUMULATIVE = "cumulative"
    INCREMENTAL = "incremental"
    AGGREGATION_UNAVAILABLE = "aggregation_unavailable"


class UsageProjection(SessionDoctorModel):
    aggregation: UsageSemantics
    row_semantics: dict[str, UsageSemantics] = Field(default_factory=dict)


class SemanticFoundation(SessionDoctorModel):
    semantic_foundation_version: str
    ordering: OrderingProjection
    capabilities: list[CapabilityEvidence] = Field(default_factory=list)
    project_identity: ProjectIdentity
    model_identity: ModelIdentity
    usage: UsageProjection


class SemanticAnalysisComponents(SessionDoctorModel):
    normalization_run_id: str
    lifecycle_observation_id: str
    lifecycle_policy_version: str
    ordering_version: str
    segmentation_version: str
    relation_rule_set_version: str
    result_rule_set_version: str
    finding_rule_set_version: str
    facet_policy_version: str
    configuration_hash: str

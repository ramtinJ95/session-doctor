from __future__ import annotations

from datetime import datetime
from pathlib import Path

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    AdapterCapabilityDeclaration,
    SemanticAnalysisComponents,
    SemanticFoundation,
    SessionSource,
)

from .connection import initialize_database, inspection_connection
from .json_values import duckdb_value, metadata_json, parse_metadata, parse_string_list
from .lifecycle import (
    LifecycleObservation,
)
from .lifecycle import (
    lifecycle_for_bundle as read_bundle_lifecycle,
)
from .lifecycle import (
    record_lifecycle_observation as write_lifecycle_observation,
)
from .migrations import require_current_schema
from .models import SessionSummary, StoreInfo
from .normalization_runs import (
    NORMALIZATION_CONFIGURATION_HASH,
    NORMALIZATION_VERSION,
    NormalizationCoverage,
    NormalizationRun,
    StoredNormalization,
    load_normalization,
    load_semantic_foundation,
    normalization_coverage,
    persist_normalization,
)
from .readers import (
    list_session_summaries,
    load_session_bundle,
    session_agent_name,
    store_info,
    table_count,
)
from .row_mappers import (
    command_run_rows,
    file_activity_rows,
    message_rows,
    model_usage_rows,
    parse_warning_rows,
    raw_event_rows,
    session_rows,
    tool_call_rows,
    tool_result_rows,
)
from .semantic_runs import (
    SemanticAnalysisRun,
    list_semantic_analysis_runs,
    record_semantic_analysis_run,
)
from .snapshot_history import (
    PruneDependencies,
    PruneResult,
    SnapshotSummary,
)
from .snapshot_history import (
    latest_snapshot as read_latest_snapshot,
)
from .snapshot_history import (
    list_snapshots as read_snapshots,
)
from .snapshot_history import (
    prune_snapshot as delete_snapshot,
)
from .snapshot_history import (
    snapshot_dependencies as read_snapshot_dependencies,
)
from .snapshot_history import (
    snapshot_summary as read_snapshot_summary,
)
from .snapshots import (
    BundleMemberCapture,
    CapturedBundle,
    CapturedSource,
    LoadedBundleMember,
)
from .snapshots import (
    add_bundle_members as write_bundle_members,
)
from .snapshots import (
    capture_source as write_source_capture,
)
from .snapshots import (
    create_single_source_bundle as write_single_source_bundle,
)
from .snapshots import (
    load_bundle_members as read_bundle_members,
)
from .snapshots import (
    load_snapshot_bytes as read_snapshot_bytes,
)
from .snapshots import load_snapshot_source as read_snapshot_source
from .writers import (
    insert_parsed_bundle as write_parsed_bundle,
)
from .writers import (
    insert_untracked_parsed_bundle as write_untracked_parsed_bundle,
)

__all__ = [
    "DuckDBStore",
    "SessionSummary",
    "StoreInfo",
    "command_run_rows",
    "duckdb_value",
    "file_activity_rows",
    "message_rows",
    "metadata_json",
    "model_usage_rows",
    "parse_metadata",
    "parse_string_list",
    "parse_warning_rows",
    "raw_event_rows",
    "session_rows",
    "tool_call_rows",
    "tool_result_rows",
]


class DuckDBStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser()

    def initialize(self) -> StoreInfo:
        initialize_database(self.database_path)
        return self.info()

    def validate_schema(self, *, allow_empty: bool = False) -> None:
        with inspection_connection(self.database_path) as connection:
            require_current_schema(connection, allow_empty=allow_empty)

    def insert_parsed_bundle(
        self,
        source: SessionSource,
        bundle: ParsedSessionBundle,
        captured_source: CapturedSource,
        captured_bundle: CapturedBundle,
        *,
        adapter_version: str = "0.1.0",
        capability_declarations: tuple[AdapterCapabilityDeclaration, ...] = (),
        terminal_evidence_ids: tuple[str, ...] = (),
    ) -> None:
        write_parsed_bundle(
            self.database_path,
            source,
            bundle,
            captured_source,
            captured_bundle,
            adapter_version=adapter_version,
            capability_declarations=capability_declarations,
            terminal_evidence_ids=terminal_evidence_ids,
        )

    def insert_untracked_parsed_bundle(
        self, source: SessionSource, bundle: ParsedSessionBundle
    ) -> None:
        write_untracked_parsed_bundle(self.database_path, source, bundle)

    def capture_source(
        self,
        source: SessionSource,
        source_bytes: bytes,
        *,
        native_modified_at: datetime | None = None,
        captured_at: datetime | None = None,
    ) -> CapturedSource:
        return write_source_capture(
            self.database_path,
            source,
            source_bytes,
            native_modified_at=native_modified_at,
            captured_at=captured_at,
        )

    def load_snapshot_bytes(self, snapshot_id: str) -> bytes | None:
        return read_snapshot_bytes(self.database_path, snapshot_id)

    def load_snapshot_source(self, snapshot_id: str) -> SessionSource | None:
        return read_snapshot_source(self.database_path, snapshot_id)

    def load_bundle_members(self, snapshot_bundle_id: str) -> tuple[LoadedBundleMember, ...]:
        return read_bundle_members(self.database_path, snapshot_bundle_id)

    def create_single_source_bundle(
        self,
        source: SessionSource,
        captured_source: CapturedSource,
        native_session_identity: str,
        native_identity_status: str = "observed",
        capture_status: str = "complete",
        primary_capture_status: str = "captured",
        capture_evidence: dict[str, object] | None = None,
    ) -> CapturedBundle:
        return write_single_source_bundle(
            self.database_path,
            source,
            captured_source,
            native_session_identity=native_session_identity,
            native_identity_status=native_identity_status,
            capture_status=capture_status,
            primary_capture_status=primary_capture_status,
            capture_evidence=capture_evidence,
        )

    def add_bundle_members(
        self,
        captured_bundle: CapturedBundle,
        members: tuple[BundleMemberCapture, ...],
    ) -> CapturedBundle:
        return write_bundle_members(self.database_path, captured_bundle, members)

    def record_lifecycle(
        self, snapshot_bundle_id: str, *, terminal_observed: bool
    ) -> LifecycleObservation:
        return write_lifecycle_observation(
            self.database_path,
            snapshot_bundle_id,
            terminal_observed=terminal_observed,
        )

    def lifecycle_for_bundle(self, snapshot_bundle_id: str) -> LifecycleObservation | None:
        return read_bundle_lifecycle(self.database_path, snapshot_bundle_id)

    def list_snapshots(
        self,
        *,
        agent_name: str | None = None,
        lifecycle_state: str | None = None,
    ) -> tuple[SnapshotSummary, ...]:
        return read_snapshots(
            self.database_path,
            agent_name=agent_name,
            lifecycle_state=lifecycle_state,
        )

    def snapshot_summary(self, snapshot_id: str) -> SnapshotSummary | None:
        return read_snapshot_summary(self.database_path, snapshot_id)

    def latest_snapshot(
        self, source_id: str, *, lifecycle_state: str | None = None
    ) -> SnapshotSummary | None:
        return read_latest_snapshot(
            self.database_path,
            source_id,
            lifecycle_state=lifecycle_state,
        )

    def prune_snapshot(self, snapshot_id: str, *, force: bool = False) -> PruneResult:
        return delete_snapshot(self.database_path, snapshot_id, force=force)

    def snapshot_dependencies(self, snapshot_id: str) -> PruneDependencies:
        return read_snapshot_dependencies(self.database_path, snapshot_id)

    def persist_normalization(
        self,
        snapshot_bundle_id: str,
        source: SessionSource,
        bundle: ParsedSessionBundle,
        *,
        adapter_version: str,
        normalization_version: str = NORMALIZATION_VERSION,
        configuration_hash: str = NORMALIZATION_CONFIGURATION_HASH,
        capability_declarations: tuple[AdapterCapabilityDeclaration, ...] = (),
        terminal_evidence_ids: tuple[str, ...] = (),
    ) -> NormalizationRun:
        return persist_normalization(
            self.database_path,
            snapshot_bundle_id,
            source,
            bundle,
            adapter_version=adapter_version,
            normalization_version=normalization_version,
            configuration_hash=configuration_hash,
            capability_declarations=capability_declarations,
            terminal_evidence_ids=terminal_evidence_ids,
        )

    def normalization_coverage(
        self,
        snapshot_bundle_id: str,
        *,
        adapter_name: str,
        adapter_version: str,
        normalization_version: str = NORMALIZATION_VERSION,
        configuration_hash: str = NORMALIZATION_CONFIGURATION_HASH,
        capability_declarations: tuple[AdapterCapabilityDeclaration, ...] = (),
    ) -> NormalizationCoverage:
        return normalization_coverage(
            self.database_path,
            snapshot_bundle_id,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            normalization_version=normalization_version,
            configuration_hash=configuration_hash,
            capability_declarations=capability_declarations,
        )

    def load_normalization(self, normalization_run_id: str) -> StoredNormalization | None:
        return load_normalization(self.database_path, normalization_run_id)

    def load_semantic_foundation(self, normalization_run_id: str) -> SemanticFoundation | None:
        return load_semantic_foundation(self.database_path, normalization_run_id)

    def record_semantic_analysis_run(
        self,
        components: SemanticAnalysisComponents,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SemanticAnalysisRun:
        return record_semantic_analysis_run(
            self.database_path,
            components,
            started_at=started_at,
            completed_at=completed_at,
            metadata=metadata,
        )

    def list_semantic_analysis_runs(self) -> tuple[SemanticAnalysisRun, ...]:
        return list_semantic_analysis_runs(self.database_path)

    def table_count(self, table_name: str) -> int:
        return table_count(self.database_path, table_name)

    def list_session_summaries(self, agent_name: str | None = None) -> tuple[SessionSummary, ...]:
        return list_session_summaries(self.database_path, agent_name)

    def session_agent_name(self, session_id: str) -> str | None:
        return session_agent_name(self.database_path, session_id)

    def load_session_bundle(self, session_id: str) -> ParsedSessionBundle | None:
        return load_session_bundle(self.database_path, session_id)

    def info(self) -> StoreInfo:
        return store_info(self.database_path)

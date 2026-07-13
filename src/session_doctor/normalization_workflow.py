from __future__ import annotations

from session_doctor.adapters import BaseAdapter
from session_doctor.adapters.base import CapturedAdapterMember
from session_doctor.store import DuckDBStore, NormalizationRun


def normalize_snapshot(
    adapter: BaseAdapter,
    store: DuckDBStore,
    snapshot_id: str,
) -> NormalizationRun:
    summary = store.snapshot_summary(snapshot_id)
    if summary is None:
        raise ValueError(f"Snapshot not found: {snapshot_id}")
    if summary.snapshot_bundle_id is None:
        raise ValueError("Unbundled snapshots cannot be normalized")
    if summary.agent_name != adapter.name.value:
        raise ValueError(f"Snapshot belongs to {summary.agent_name}, not {adapter.name.value}")
    members = store.load_bundle_members(summary.snapshot_bundle_id)
    if not members or members[0].member_role != "primary":
        raise ValueError("Snapshot bundle has no primary member")
    primary = members[0]
    if primary.source is None or primary.source_bytes is None:
        raise ValueError("Snapshot bundle primary bytes are unavailable")
    captured_members = tuple(
        CapturedAdapterMember(member.source, member.member_role, member.source_bytes)
        for member in members
        if member.source is not None and member.source_bytes is not None
    )
    prepared_source = adapter.prepare_captured_source(primary.source, captured_members)
    bundle = adapter.parse_source(prepared_source, primary.source_bytes)
    return store.persist_normalization(
        summary.snapshot_bundle_id,
        primary.source,
        bundle,
        adapter_version=adapter.version,
        capability_declarations=adapter.capabilities,
        terminal_evidence_ids=adapter.terminal_evidence_ids(bundle),
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from session_doctor.ids import stable_id

from .connection import read_connection, transaction, write_connection
from .json_values import metadata_json, parse_metadata

LIFECYCLE_POLICY_VERSION = "lifecycle-v1"
SETTLING_INTERVAL = timedelta(seconds=30)
FINALIZED_LIFECYCLE_STATES = ("terminal_observed", "settled_unknown")
LIFECYCLE_STATES = (
    "terminal_observed",
    "settled_unknown",
    "possibly_active",
    "snapshot_incomplete",
)


@dataclass(frozen=True)
class LifecycleObservation:
    lifecycle_observation_id: str
    snapshot_bundle_id: str
    state: str
    observed_at: object
    evidence: dict[str, object]


def record_lifecycle_observation(
    database_path: Path,
    snapshot_bundle_id: str,
    *,
    terminal_observed: bool,
) -> LifecycleObservation:
    with write_connection(database_path) as connection, transaction(connection):
        current = connection.execute(
            """
            SELECT b.bundle_content_id, b.agent_name, b.native_session_identity,
                c.lineage_capture_sequence, c.previous_lineage_bundle_id,
                b.captured_at, c.capture_status
            FROM snapshot_bundles AS b
            JOIN bundle_capture_metadata AS c USING (snapshot_bundle_id)
            WHERE b.snapshot_bundle_id = ?
            """,
            [snapshot_bundle_id],
        ).fetchone()
        if current is None:
            raise ValueError(f"Snapshot bundle not found: {snapshot_bundle_id}")
        (
            bundle_content_id,
            agent_name,
            native_session_identity,
            capture_sequence,
            previous_bundle_id,
            captured_at,
            capture_status,
        ) = current
        previous = None
        if previous_bundle_id is not None:
            previous = connection.execute(
                """
                SELECT b.bundle_content_id, c.lineage_capture_sequence,
                    b.captured_at, c.capture_status
                FROM snapshot_bundles AS b
                JOIN bundle_capture_metadata AS c USING (snapshot_bundle_id)
                WHERE b.snapshot_bundle_id = ?
                """,
                [previous_bundle_id],
            ).fetchone()
        primary = connection.execute(
            """
            SELECT s.logical_source_id, s.capture_sequence
            FROM snapshot_bundles AS b
            JOIN source_snapshots AS s ON s.snapshot_id = b.primary_snapshot_id
            WHERE b.snapshot_bundle_id = ?
            """,
            [snapshot_bundle_id],
        ).fetchone()
        immediately_previous_bundle_id = None
        if primary is not None:
            immediately_previous = connection.execute(
                """
                SELECT b.snapshot_bundle_id
                FROM source_snapshots AS s
                LEFT JOIN snapshot_bundles AS b ON b.primary_snapshot_id = s.snapshot_id
                WHERE s.logical_source_id = ? AND s.capture_sequence < ?
                ORDER BY s.capture_sequence DESC LIMIT 1
                """,
                [primary[0], primary[1]],
            ).fetchone()
            immediately_previous_bundle_id = (
                immediately_previous[0] if immediately_previous else None
            )
        lineage_is_consecutive = immediately_previous_bundle_id == previous_bundle_id

        if capture_status != "complete":
            state = "snapshot_incomplete"
            reason = f"capture_{capture_status}"
        elif terminal_observed:
            state = "terminal_observed"
            reason = "native_terminal_evidence"
        elif _qualifies_as_settled(current, previous, lineage_is_consecutive):
            state = "settled_unknown"
            reason = "consecutive_identical_complete_bundles"
        else:
            state = "possibly_active"
            reason = "awaiting_terminal_or_identical_recapture"

        evidence: dict[str, object] = {
            "reason": reason,
            "agent_name": str(agent_name),
            "native_session_identity": str(native_session_identity),
            "capture_sequence": int(capture_sequence),
            "bundle_content_id": str(bundle_content_id),
            "capture_status": str(capture_status),
            "previous_snapshot_bundle_id": (
                str(previous_bundle_id) if previous_bundle_id is not None else None
            ),
            "lineage_is_source_consecutive": lineage_is_consecutive,
        }
        observation_id = stable_id(
            "lifecycle-observation",
            snapshot_bundle_id,
            LIFECYCLE_POLICY_VERSION,
            state,
        )
        connection.execute(
            """
            INSERT INTO lifecycle_observations (
                lifecycle_observation_id, snapshot_bundle_id,
                lifecycle_policy_version, state, observed_at, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                observation_id,
                snapshot_bundle_id,
                LIFECYCLE_POLICY_VERSION,
                state,
                captured_at,
                metadata_json(evidence),
            ],
        )
    return LifecycleObservation(
        lifecycle_observation_id=observation_id,
        snapshot_bundle_id=snapshot_bundle_id,
        state=state,
        observed_at=captured_at,
        evidence=evidence,
    )


def _qualifies_as_settled(
    current: tuple[object, ...],
    previous: tuple[object, ...] | None,
    lineage_is_consecutive: bool,
) -> bool:
    if previous is None or not lineage_is_consecutive:
        return False
    current_content_id = current[0]
    current_sequence = int(str(current[3]))
    current_captured_at = current[5]
    previous_content_id, previous_sequence, previous_captured_at, previous_status = previous
    if not isinstance(current_captured_at, datetime) or not isinstance(
        previous_captured_at, datetime
    ):
        return False
    return bool(
        previous_status == "complete"
        and previous_content_id == current_content_id
        and int(str(previous_sequence)) + 1 == current_sequence
        and current_captured_at - previous_captured_at >= SETTLING_INTERVAL
    )


def lifecycle_for_bundle(
    database_path: Path, snapshot_bundle_id: str
) -> LifecycleObservation | None:
    with read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT lifecycle_observation_id, snapshot_bundle_id, state,
                observed_at, evidence_json
            FROM lifecycle_observations
            WHERE snapshot_bundle_id = ?
            """,
            [snapshot_bundle_id],
        ).fetchone()
    if row is None:
        return None
    return LifecycleObservation(
        lifecycle_observation_id=str(row[0]),
        snapshot_bundle_id=str(row[1]),
        state=str(row[2]),
        observed_at=row[3],
        evidence=parse_metadata(row[4]),
    )

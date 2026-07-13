from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from session_doctor.ids import stable_id
from session_doctor.schemas import SessionSource

from .connection import read_connection, transaction, write_connection
from .json_values import metadata_json, parse_metadata

SNAPSHOT_CODEC = "zlib"
SNAPSHOT_COMPRESSION_LEVEL = 6


@dataclass(frozen=True)
class CapturedSource:
    blob_id: str
    logical_source_id: str
    snapshot_id: str
    snapshot_content_id: str
    capture_sequence: int
    captured_at: datetime


@dataclass(frozen=True)
class CapturedBundle:
    snapshot_bundle_id: str
    bundle_content_id: str
    native_session_identity: str
    capture_sequence: int
    native_identity_status: str


class SnapshotSourceMismatchError(RuntimeError):
    pass


def capture_source(
    database_path: Path,
    source: SessionSource,
    source_bytes: bytes,
    *,
    native_modified_at: datetime | None = None,
) -> CapturedSource:
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    blob_id = stable_id("source-blob", "sha256", content_hash)
    logical_source_id = stable_id(
        "logical-source",
        source.agent_name.value,
        source.source_kind.value,
        source.source_id,
    )
    snapshot_content_id = stable_id("snapshot-content", logical_source_id, blob_id)
    captured_at = datetime.now(UTC)
    with write_connection(database_path) as connection, transaction(connection):
        connection.execute(
            """
            INSERT INTO source_blobs (
                blob_id, content_hash, codec, compressed_bytes, original_byte_length
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                blob_id,
                content_hash,
                SNAPSHOT_CODEC,
                zlib.compress(source_bytes, level=SNAPSHOT_COMPRESSION_LEVEL),
                len(source_bytes),
            ],
        )
        connection.execute(
            """
            INSERT INTO logical_sources (
                logical_source_id, agent_name, source_kind, source_path, metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                logical_source_id,
                source.agent_name.value,
                source.source_kind.value,
                source.source_path,
                metadata_json(source.metadata),
            ],
        )
        previous = connection.execute(
            """
            SELECT snapshot_id, capture_sequence
            FROM source_snapshots
            WHERE logical_source_id = ?
            ORDER BY capture_sequence DESC
            LIMIT 1
            """,
            [logical_source_id],
        ).fetchone()
        capture_sequence = int(previous[1]) + 1 if previous else 1
        previous_snapshot_id = str(previous[0]) if previous else None
        snapshot_id = stable_id(
            "source-snapshot",
            logical_source_id,
            capture_sequence,
            captured_at.isoformat(),
            snapshot_content_id,
        )
        connection.execute(
            """
            INSERT INTO source_snapshots (
                snapshot_id, source_id, agent_name, source_kind, source_path,
                discovered_at, native_session_id, parent_source_id, source_metadata_json,
                logical_source_id, blob_id, snapshot_content_id,
                capture_sequence, captured_at, native_modified_at, capture_status,
                previous_snapshot_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'captured', ?)
            """,
            [
                snapshot_id,
                source.source_id,
                source.agent_name.value,
                source.source_kind.value,
                source.source_path,
                source.discovered_at.isoformat() if source.discovered_at else None,
                source.native_session_id,
                source.parent_source_id,
                metadata_json(source.metadata),
                logical_source_id,
                blob_id,
                snapshot_content_id,
                capture_sequence,
                captured_at,
                native_modified_at,
                previous_snapshot_id,
            ],
        )

    return CapturedSource(
        blob_id=blob_id,
        logical_source_id=logical_source_id,
        snapshot_id=snapshot_id,
        snapshot_content_id=snapshot_content_id,
        capture_sequence=capture_sequence,
        captured_at=captured_at,
    )


def create_single_source_bundle(
    database_path: Path,
    source: SessionSource,
    captured_source: CapturedSource,
    *,
    native_session_identity: str,
    native_identity_status: str = "observed",
) -> CapturedBundle:
    stored_source = load_snapshot_source(database_path, captured_source.snapshot_id)
    if stored_source is None or not source_descriptors_match(stored_source, source):
        raise SnapshotSourceMismatchError("snapshot does not belong to supplied source")
    with write_connection(database_path) as connection, transaction(connection):
        snapshot_row = connection.execute(
            """
            SELECT logical_source_id, blob_id, snapshot_content_id, capture_sequence,
                captured_at
            FROM source_snapshots
            WHERE snapshot_id = ?
            """,
            [captured_source.snapshot_id],
        ).fetchone()
        if snapshot_row is None or snapshot_row[:4] != (
            captured_source.logical_source_id,
            captured_source.blob_id,
            captured_source.snapshot_content_id,
            captured_source.capture_sequence,
        ):
            raise SnapshotSourceMismatchError("captured source identity does not match storage")
        stored_captured_at = snapshot_row[4]
        previous_bundle = connection.execute(
            """
            SELECT snapshot_bundle_id, native_bundle_capture_sequence
            FROM snapshot_bundles
            WHERE agent_name = ? AND native_session_identity = ?
            ORDER BY native_bundle_capture_sequence DESC
            LIMIT 1
            """,
            [stored_source.agent_name.value, native_session_identity],
        ).fetchone()
        bundle_sequence = int(previous_bundle[1]) + 1 if previous_bundle else 1
        previous_bundle_id = str(previous_bundle[0]) if previous_bundle else None
        bundle_content_id = stable_id(
            "bundle-content",
            stored_source.agent_name.value,
            native_session_identity,
            "primary",
            captured_source.logical_source_id,
            captured_source.snapshot_content_id,
            "captured",
        )
        snapshot_bundle_id = stable_id(
            "snapshot-bundle",
            stored_source.agent_name.value,
            native_session_identity,
            bundle_sequence,
            bundle_content_id,
        )
        connection.execute(
            """
            INSERT INTO snapshot_bundles (
                snapshot_bundle_id, bundle_content_id, agent_name,
                native_session_identity, primary_snapshot_id, native_identity_status,
                native_bundle_capture_sequence,
                previous_snapshot_bundle_id, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snapshot_bundle_id,
                bundle_content_id,
                stored_source.agent_name.value,
                native_session_identity,
                captured_source.snapshot_id,
                native_identity_status,
                bundle_sequence,
                previous_bundle_id,
                stored_captured_at,
            ],
        )
        connection.execute(
            """
            INSERT INTO snapshot_bundle_members (
                snapshot_bundle_id, logical_source_id, snapshot_id,
                capture_order, member_role, member_capture_status
            ) VALUES (?, ?, ?, 0, 'primary', 'captured')
            """,
            [
                snapshot_bundle_id,
                captured_source.logical_source_id,
                captured_source.snapshot_id,
            ],
        )
    return CapturedBundle(
        snapshot_bundle_id=snapshot_bundle_id,
        bundle_content_id=bundle_content_id,
        native_session_identity=native_session_identity,
        capture_sequence=bundle_sequence,
        native_identity_status=native_identity_status,
    )


def load_snapshot_bytes(database_path: Path, snapshot_id: str) -> bytes | None:
    with read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT b.codec, b.compressed_bytes, b.original_byte_length, b.content_hash
            FROM source_snapshots AS s
            JOIN source_blobs AS b ON b.blob_id = s.blob_id
            WHERE s.snapshot_id = ?
            """,
            [snapshot_id],
        ).fetchone()
    if row is None:
        return None
    codec, compressed_bytes, original_byte_length, content_hash = row
    if codec != SNAPSHOT_CODEC:
        raise ValueError(f"Unsupported snapshot codec: {codec}")
    source_bytes = zlib.decompress(bytes(compressed_bytes))
    if len(source_bytes) != int(original_byte_length):
        raise ValueError("Snapshot byte length does not match stored metadata")
    if hashlib.sha256(source_bytes).hexdigest() != content_hash:
        raise ValueError("Snapshot content hash does not match stored metadata")
    return source_bytes


def load_snapshot_source(database_path: Path, snapshot_id: str) -> SessionSource | None:
    with read_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT source_id, agent_name, source_path, source_kind,
                discovered_at, native_session_id, parent_source_id, source_metadata_json
            FROM source_snapshots
            WHERE snapshot_id = ?
            """,
            [snapshot_id],
        ).fetchone()
    if row is None:
        return None
    return SessionSource.model_validate(
        {
            "source_id": row[0],
            "agent_name": row[1],
            "source_path": row[2],
            "source_kind": row[3],
            "discovered_at": row[4],
            "native_session_id": row[5],
            "parent_source_id": row[6],
            "metadata": parse_metadata(row[7]),
        }
    )


def source_descriptors_match(left: SessionSource, right: SessionSource) -> bool:
    return (
        left.source_id == right.source_id
        and left.agent_name is right.agent_name
        and left.source_path == right.source_path
        and left.source_kind is right.source_kind
        and left.discovered_at == right.discovered_at
        and left.native_session_id == right.native_session_id
        and left.parent_source_id == right.parent_source_id
        and left.metadata == right.metadata
    )

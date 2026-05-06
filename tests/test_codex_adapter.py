from __future__ import annotations

from pathlib import Path

from session_doctor.adapters.codex import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
    CodexAdapter,
)
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, NormalizedRole, SessionSource

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, path),
        agent_name=AgentName.CODEX,
        source_path=str(path),
    )


def test_codex_parse_source_normalizes_core_records() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    assert bundle.session is not None
    assert bundle.session.native_session_id == "codex-session-1"
    assert bundle.session.cwd == "/tmp/session-doctor"
    assert bundle.session.model == "gpt-5.4"
    assert len(bundle.raw_events) == 11
    assert len(bundle.messages) == 2
    assert len(bundle.tool_calls) == 1
    assert len(bundle.tool_results) == 1
    assert len(bundle.command_runs) == 1
    assert len(bundle.file_activities) == 1


def test_codex_parse_source_uses_response_item_as_canonical_message_source() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    message_sources = [message.metadata["codex_message_source"] for message in bundle.messages]
    assert message_sources == [
        CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
        CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
    ]
    assert bundle.session is not None
    assert bundle.session.metadata["codex_message_source_counts"] == {
        CODEX_MESSAGE_SOURCE_RESPONSE_ITEM: 2,
        CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK: 0,
    }


def test_codex_parse_source_uses_event_msg_messages_as_fallback() -> None:
    fixture_path = FIXTURE_DIR / "event-msg-fallback.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    assert [(message.role, message.text) for message in bundle.messages] == [
        (NormalizedRole.USER, "Only event message user text exists."),
        (NormalizedRole.ASSISTANT, "Only event message assistant text exists."),
    ]
    assert {message.metadata["codex_message_source"] for message in bundle.messages} == {
        CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK
    }
    assert bundle.session is not None
    assert bundle.session.metadata["codex_message_source_counts"] == {
        CODEX_MESSAGE_SOURCE_RESPONSE_ITEM: 0,
        CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK: 2,
    }


def test_codex_parse_source_records_command_and_patch_metadata() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    command_run = bundle.command_runs[0]
    assert command_run.command == "pytest -q"
    assert command_run.exit_code == 1
    assert command_run.output_length == len("failed")

    file_activity = bundle.file_activities[0]
    assert file_activity.path == "/tmp/session-doctor/src/example.py"
    assert file_activity.operation == "update"
    assert file_activity.metadata["success"] is True
    assert file_activity.metadata["diff_length"] == len("@@\n-old\n+new\n")


def test_codex_parse_source_emits_warnings_without_stopping() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    warning_codes = {warning.metadata["code"] for warning in bundle.parse_warnings}
    assert {"malformed_json", "unsupported_record_type"}.issubset(warning_codes)
    assert bundle.session is not None
    assert bundle.session.metadata["compacted_record_count"] == 1

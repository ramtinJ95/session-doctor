from __future__ import annotations

import json
from pathlib import Path

from session_doctor.adapters.codex import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
    CodexAdapter,
)
from session_doctor.ids import source_id_for_path, stable_id
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
    assert len(bundle.raw_events) == 17
    assert len(bundle.messages) == 2
    assert len(bundle.tool_calls) == 2
    assert len(bundle.tool_results) == 2
    assert len(bundle.command_runs) == 1
    assert len(bundle.file_activities) == 1
    assert len(bundle.model_usage) == 1


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
    assert command_run.command == "/bin/zsh -lc 'pytest -q'"
    assert command_run.command_display == "pytest -q"
    assert command_run.command_normalization == "shell_wrapper:zsh:-lc"
    assert command_run.exit_code == 1
    assert command_run.output_length == len("failed")
    assert command_run.stdout_hash is not None
    assert command_run.metadata["output_source"] == "aggregated_output"
    assert command_run.tool_call_id == stable_id("tool_call", command_run.session_id, "call-1")

    file_activity = bundle.file_activities[0]
    assert file_activity.path == "/tmp/session-doctor/src/example.py"
    assert file_activity.canonical_path == "/tmp/session-doctor/src/example.py"
    assert file_activity.project_relative_path == "src/example.py"
    assert file_activity.path_resolution == "absolute"
    assert file_activity.operation == "update"
    assert file_activity.metadata["success"] is True
    assert file_activity.metadata["diff_length"] == len("@@\n-old\n+new\n")


def test_codex_parse_source_normalizes_expected_common_shapes() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    web_search_call = next(
        tool_call for tool_call in bundle.tool_calls if tool_call.name == "web_search"
    )
    assert web_search_call.metadata["query"] == "codex docs"

    web_search_result = next(
        tool_result
        for tool_result in bundle.tool_results
        if tool_result.metadata.get("tool_name") == "web_search"
    )
    assert web_search_result.native_tool_call_id == "ws-1"
    assert web_search_result.tool_call_id is None
    assert web_search_result.metadata["query"] == "codex docs"

    usage = bundle.model_usage[0]
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 2
    assert usage.total_tokens == 15
    assert usage.metadata["reasoning_output_tokens"] == 1

    assert bundle.session is not None
    assert bundle.session.metadata["codex_expected_ignored_counts"] == {
        "event_msg.task_complete": 1,
        "event_msg.task_started": 1,
        "response_item.reasoning": 1,
    }


def test_codex_parse_source_emits_warnings_without_stopping() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_source(source_for_fixture(fixture_path))

    warning_codes = {warning.metadata["code"] for warning in bundle.parse_warnings}
    assert {"malformed_json", "unsupported_record_type"}.issubset(warning_codes)
    assert len(bundle.parse_warnings) == 2
    assert bundle.session is not None
    assert bundle.session.metadata["compacted_record_count"] == 1


def test_codex_parse_source_keeps_repeated_fallback_messages_across_turns(tmp_path) -> None:
    session_path = tmp_path / "repeat.jsonl"
    records = [
        {
            "timestamp": "2026-05-06T08:00:00Z",
            "type": "session_meta",
            "payload": {"id": "repeat-session", "cwd": "/tmp"},
        },
        {
            "timestamp": "2026-05-06T08:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "same request"}],
            },
        },
        {
            "timestamp": "2026-05-06T08:00:02Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "same request"},
        },
        {
            "timestamp": "2026-05-06T08:01:00Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "same request"},
        },
    ]
    session_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    bundle = CodexAdapter().parse_source(source_for_fixture(session_path))

    assert [(message.role, message.text) for message in bundle.messages] == [
        (NormalizedRole.USER, "same request"),
        (NormalizedRole.USER, "same request"),
    ]
    assert bundle.messages[0].metadata["codex_message_source"] == CODEX_MESSAGE_SOURCE_RESPONSE_ITEM
    assert (
        bundle.messages[1].metadata["codex_message_source"]
        == CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK
    )


def test_codex_facade_preserves_helper_imports() -> None:
    from session_doctor.adapters import codex
    from session_doctor.adapters.codex import command_output_parts, command_text

    assert command_text(["python", "-m", "pytest"]) == "python -m pytest"
    assert command_output_parts({"aggregated_output": "failed"}) == (
        "failed",
        "",
        "aggregated_output",
    )
    assert "command_text" in codex.__all__
    assert "session_id_from_filename" in codex.__all__

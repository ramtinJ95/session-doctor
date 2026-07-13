from __future__ import annotations

import json
from pathlib import Path

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.codex import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
    CodexAdapter,
)
from session_doctor.adapters.codex_files import file_activities_from_patch_event
from session_doctor.adapters.codex_metadata import extract_session_metadata
from session_doctor.adapters.codex_tools import model_usage_from_token_count
from session_doctor.ids import source_id_for_path, stable_id
from session_doctor.schemas import (
    AgentName,
    NormalizedRole,
    RawEvent,
    SessionSource,
    UsageSemantics,
)
from session_doctor.semantic_foundations import derive_model_identity

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, path),
        agent_name=AgentName.CODEX,
        source_path=str(path),
    )


def test_codex_terminal_state_uses_latest_task_lifecycle_event() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    fixture_bytes = fixture_path.read_bytes()
    resumed = fixture_bytes + (
        b'\n{"type":"event_msg","payload":{"type":"task_started","turn_id":"turn-2"}}'
    )
    completed_again = resumed + (
        b'\n{"type":"event_msg","payload":{"type":"task_complete","turn_id":"turn-2"}}'
    )

    adapter = CodexAdapter()
    assert adapter.terminal_observed(source, fixture_bytes) is True
    assert adapter.terminal_observed(source, resumed) is False
    assert adapter.terminal_observed(source, completed_again) is True


def test_codex_turn_membership_is_not_a_parent_edge() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))
    task_event = next(
        event for event in bundle.raw_events if event.metadata.get("payload_type") == "task_started"
    )

    assert task_event.native_parent_id is None
    assert task_event.metadata["turn_id"] == "turn-1"


def test_codex_empty_token_count_is_aggregation_unavailable() -> None:
    event = RawEvent(
        event_id="event-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
        record_index=0,
    )

    usage = model_usage_from_token_count("session-1", event, {"info": {}})

    assert usage.aggregation_semantics is UsageSemantics.AGGREGATION_UNAVAILABLE


def test_codex_provider_transition_preserves_latest_pair_and_mixed_identity(tmp_path) -> None:
    source_path = tmp_path / "session.jsonl"
    source = source_for_fixture(source_path)
    records = [
        (0, {"type": "session_meta", "payload": {"id": "native", "model_provider": "old"}}),
        (
            1,
            {
                "type": "turn_context",
                "payload": {"model_provider": "old", "model": "model-a"},
            },
        ),
        (
            2,
            {
                "type": "turn_context",
                "payload": {"model_provider": "new", "model": "model-a"},
            },
        ),
    ]

    metadata = extract_session_metadata(source, source_path, records)
    identity = derive_model_identity(ParsedSessionBundle(session=metadata.session))

    assert metadata.session.model_provider == "new"
    assert metadata.session.model == "model-a"
    assert [(row.provider, row.model) for row in identity.models] == [
        ("new", "model-a"),
        ("old", "model-a"),
    ]


def test_codex_parse_source_normalizes_core_records() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

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


def test_codex_empty_patch_uses_an_explicit_missing_path_marker() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))
    assert bundle.session is not None

    activities = file_activities_from_patch_event(
        bundle.session.session_id,
        bundle.raw_events[0],
        {"changes": {}},
        cwd=bundle.session.cwd,
        project_path=bundle.session.project_path,
    )

    assert len(activities) == 1
    assert activities[0].path == "unknown"
    assert activities[0].canonical_path is None
    assert activities[0].path_resolution == "unresolved"
    assert activities[0].metadata["path_missing"] is True


def test_codex_parse_source_normalizes_expected_common_shapes() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

    warning_codes = {warning.metadata["code"] for warning in bundle.parse_warnings}
    assert {"malformed_json", "unsupported_record_type"}.issubset(warning_codes)
    assert len(bundle.parse_warnings) == 2
    assert bundle.session is not None
    assert bundle.session.metadata["compacted_record_count"] == 1


def test_codex_handles_current_metadata_drift_without_unsupported_warnings(tmp_path) -> None:
    session_path = tmp_path / "metadata-drift.jsonl"
    records = [
        {
            "timestamp": "2026-07-10T10:00:00Z",
            "type": "session_meta",
            "payload": {"id": "metadata-drift", "cwd": "/tmp"},
        },
        *(
            {
                "timestamp": f"2026-07-10T10:00:0{index}Z",
                "type": "event_msg",
                "payload": {"type": payload_type},
            }
            for index, payload_type in enumerate(
                (
                    "context_compacted",
                    "entered_review_mode",
                    "exited_review_mode",
                    "thread_settings_applied",
                ),
                start=1,
            )
        ),
        {
            "timestamp": "2026-07-10T10:00:05Z",
            "type": "world_state",
            "payload": {"state": "PRIVATE_WORLD_STATE"},
        },
        {
            "timestamp": "2026-07-10T10:00:06Z",
            "type": "event_msg",
            "payload": {"type": "turn_aborted", "reason": "PRIVATE_ABORT_REASON"},
        },
    ]
    session_path.write_text("\n".join(json.dumps(record) for record in records))

    bundle = CodexAdapter().parse_live_source(source_for_fixture(session_path))

    assert bundle.session is not None
    assert bundle.session.metadata["codex_expected_ignored_counts"] == {
        "event_msg.context_compacted": 1,
        "event_msg.entered_review_mode": 1,
        "event_msg.exited_review_mode": 1,
        "event_msg.thread_settings_applied": 1,
        "record.world_state": 1,
    }
    assert bundle.session.metadata["compacted_record_count"] == 1
    assert [warning.metadata["code"] for warning in bundle.parse_warnings] == ["codex_turn_aborted"]
    assert "PRIVATE_WORLD_STATE" not in bundle.model_dump_json()
    assert "PRIVATE_ABORT_REASON" not in bundle.model_dump_json()


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

    bundle = CodexAdapter().parse_live_source(source_for_fixture(session_path))

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


def test_codex_normalizes_current_response_item_commands() -> None:
    fixture_path = FIXTURE_DIR / "current-response-items.jsonl"
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

    assert len(bundle.command_runs) == 4
    assert len(bundle.tool_calls) == 6
    assert len(bundle.tool_results) == 6
    assert [command.metadata["execution_kind"] for command in bundle.command_runs].count(
        "exec_command"
    ) == 3
    failed = next(command for command in bundle.command_runs if command.exit_code == 1)
    running = next(
        command for command in bundle.command_runs if command.metadata["outcome"] == "running"
    )
    opaque = next(
        command for command in bundle.command_runs if command.metadata["execution_kind"] == "exec"
    )
    successful = next(command for command in bundle.command_runs if command.exit_code == 0)
    assert failed.command == "pytest synthetic_tests -q"
    assert failed.cwd == "/tmp/codex-native-contract"
    assert failed.output_length == len("Synthetic failing output")
    assert failed.stdout_hash is not None
    assert failed.ended_at is not None
    failed_tool_result = next(
        result for result in bundle.tool_results if result.tool_call_id == failed.tool_call_id
    )
    assert failed_tool_result.is_error is True
    successful_tool_result = next(
        result for result in bundle.tool_results if result.tool_call_id == successful.tool_call_id
    )
    assert successful.command == "python -m synthetic_check"
    assert successful_tool_result.is_error is False
    assert running.command == "sleep 1"
    assert running.exit_code is None
    assert running.ended_at is None
    assert opaque.command == "git status --short"
    assert opaque.exit_code is None
    assert opaque.metadata["outcome"] == "opaque"
    assert all(command.tool_call_id is not None for command in bundle.command_runs)
    tool_search = next(call for call in bundle.tool_calls if call.name == "tool_search")
    tool_search_result = next(
        result for result in bundle.tool_results if result.tool_call_id == tool_search.tool_call_id
    )
    assert tool_search.metadata == {
        "payload_type": "tool_search_call",
        "status": "completed",
        "argument_keys": ["query"],
        "execution": "client",
    }
    assert tool_search_result.output_hash is not None
    assert tool_search_result.output_length is not None
    assert tool_search_result.output_length > 0
    assert tool_search_result.metadata == {
        "payload_type": "tool_search_output",
        "status": "completed",
        "execution": "client",
        "tool_count": 1,
    }
    assert bundle.session is not None
    assert bundle.session.metadata["codex_expected_ignored_counts"] == {
        "event_msg.mcp_tool_call_end": 1,
        "event_msg.sub_agent_activity": 1,
        "record.inter_agent_communication_metadata": 1,
        "response_item.agent_message": 1,
    }
    assert bundle.parse_warnings == []
    serialized = bundle.model_dump_json()
    assert "Synthetic failing output" not in serialized
    assert "Synthetic opaque exec output" not in serialized
    assert "synthetic-chunk" not in serialized
    assert "Synthetic inter-agent message" not in serialized
    assert "synthetic-agent-a" not in serialized
    assert "synthetic_tool" not in serialized
    assert "synthetic-server" not in serialized
    assert "synthetic-result" not in serialized


def test_codex_response_item_cardinality_is_explicit() -> None:
    fixture_path = FIXTURE_DIR / "current-response-item-ambiguities.jsonl"
    bundle = CodexAdapter().parse_live_source(source_for_fixture(fixture_path))

    warning_codes = [warning.metadata["code"] for warning in bundle.parse_warnings]
    assert len(bundle.tool_calls) == 2
    assert len(bundle.tool_results) == 3
    assert len(bundle.command_runs) == 2
    assert warning_codes == [
        "ambiguous_codex_tool_call",
        "missing_codex_tool_result",
        "ambiguous_codex_tool_results",
        "orphan_codex_tool_result",
    ]
    assert {command.metadata["outcome"] for command in bundle.command_runs} == {
        "ambiguous",
        "missing",
    }
    assert sum(result.tool_call_id is None for result in bundle.tool_results) == 1
    assert all(
        set(warning.metadata).issubset(
            {"code", "call_count", "result_count", "reason", "execution_kind"}
        )
        for warning in bundle.parse_warnings
    )


def test_codex_legacy_command_wins_over_response_item(tmp_path) -> None:
    session_path = tmp_path / "legacy-precedence.jsonl"
    records = [
        {
            "timestamp": "2026-07-10T10:00:00Z",
            "type": "session_meta",
            "payload": {"id": "legacy-precedence", "cwd": "/tmp"},
        },
        {
            "timestamp": "2026-07-10T10:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "synthetic response command"}),
                "call_id": "shared-call",
            },
        },
        {
            "timestamp": "2026-07-10T10:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "shared-call",
                "output": "Synthetic response output",
            },
        },
        {
            "timestamp": "2026-07-10T10:00:03Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command_end",
                "call_id": "shared-call",
                "command": "synthetic legacy command",
                "exit_code": 0,
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(record) for record in records))

    bundle = CodexAdapter().parse_live_source(source_for_fixture(session_path))

    assert len(bundle.tool_calls) == 1
    assert len(bundle.tool_results) == 1
    assert len(bundle.command_runs) == 1
    assert bundle.command_runs[0].command == "synthetic legacy command"
    assert bundle.command_runs[0].exit_code == 0


def test_codex_rejects_malformed_response_command_without_guessing(tmp_path) -> None:
    session_path = tmp_path / "malformed-response-command.jsonl"
    records = [
        {
            "timestamp": "2026-07-10T10:00:00Z",
            "type": "session_meta",
            "payload": {"id": "malformed-response-command", "cwd": "/tmp"},
        },
        {
            "timestamp": "2026-07-10T10:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": "PRIVATE_NOT_JSON_COMMAND",
                "call_id": "malformed-call",
            },
        },
        {
            "timestamp": "2026-07-10T10:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "malformed-call",
                "output": "PRIVATE_MALFORMED_OUTPUT",
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(record) for record in records))

    bundle = CodexAdapter().parse_live_source(source_for_fixture(session_path))

    assert len(bundle.tool_calls) == 1
    assert len(bundle.tool_results) == 1
    assert bundle.command_runs == []
    assert [warning.metadata for warning in bundle.parse_warnings] == [
        {
            "code": "invalid_codex_response_command",
            "reason": "arguments_not_json",
            "execution_kind": "exec_command",
        }
    ]
    serialized_warnings = json.dumps(
        [warning.model_dump(mode="json") for warning in bundle.parse_warnings]
    )
    assert "PRIVATE_NOT_JSON_COMMAND" not in serialized_warnings
    assert "PRIVATE_MALFORMED_OUTPUT" not in serialized_warnings

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from session_doctor.adapters.pi import PiAdapter
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, NormalizedRole, SessionSource

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pi"


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.PI, path),
        agent_name=AgentName.PI,
        source_path=str(path),
    )


def test_pi_parse_source_normalizes_session_raw_events_and_messages() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_source(source_for_fixture(fixture_path))

    assert bundle.session is not None
    assert bundle.session.native_session_id == "pi-session-1"
    assert bundle.session.cwd == "/tmp/session-doctor"
    assert bundle.session.model_provider == "openai-codex"
    assert bundle.session.model == "gpt-5.4"
    assert len(bundle.raw_events) == 15
    assert [(message.role, message.text) for message in bundle.messages] == [
        (NormalizedRole.USER, "Please fix the failing pytest in tests/test_cli.py"),
        (NormalizedRole.ASSISTANT, "I will inspect the failing test."),
        (NormalizedRole.TOOL, "failed"),
        (NormalizedRole.TOOL, None),
    ]


def test_pi_parse_source_preserves_message_metadata_and_block_types() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_source(source_for_fixture(fixture_path))

    assistant_message = next(
        message for message in bundle.messages if message.role is NormalizedRole.ASSISTANT
    )

    assert assistant_message.native_message_id == "assistant-message-1"
    assert assistant_message.parent_message_id == "user-message-1"
    assert assistant_message.text_hash is not None
    assert assistant_message.text_length == len("I will inspect the failing test.")
    assert assistant_message.content_block_types == [
        "thinking",
        "text",
        "toolCall",
        "toolCall",
        "toolCall",
        "toolCall",
    ]
    assert assistant_message.metadata["pi_message_role"] == "assistant"
    assert assistant_message.metadata["stop_reason"] == "tool_use"


def test_pi_parse_source_normalizes_tools_commands_files_and_usage() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_source(source_for_fixture(fixture_path))

    assert [tool_call.name for tool_call in bundle.tool_calls] == [
        "bash",
        "read",
        "edit",
        "write",
    ]
    assert all(tool_call.arguments_hash is not None for tool_call in bundle.tool_calls)
    assert bundle.tool_calls[1].metadata["path"] == "tests/test_cli.py"

    assert len(bundle.tool_results) == 1
    assert bundle.tool_results[0].tool_call_id == bundle.tool_calls[0].tool_call_id
    assert bundle.tool_results[0].output_hash is not None
    assert bundle.tool_results[0].output_length == len("failed")

    assert [(command.command, command.exit_code) for command in bundle.command_runs] == [
        ("pytest tests/test_cli.py -q", 1),
    ]
    assert bundle.command_runs[0].metadata["source"] == "bashExecution"
    assert bundle.command_runs[0].tool_call_id == bundle.tool_calls[0].tool_call_id

    assert [(activity.path, activity.operation) for activity in bundle.file_activities] == [
        ("tests/test_cli.py", "read"),
        ("tests/test_cli.py", "edit"),
        ("scratch/output.txt", "write"),
    ]
    assert bundle.file_activities[1].content_hash is not None
    assert bundle.file_activities[1].metadata["content_length"] > 0
    assert bundle.file_activities[2].content_hash is not None

    assert len(bundle.model_usage) == 1
    usage = bundle.model_usage[0]
    assert usage.provider == "openai-codex"
    assert usage.model == "gpt-5.4"
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.cache_read_tokens == 5
    assert usage.cache_write_tokens == 0
    assert usage.total_tokens == 125
    assert usage.cost == Decimal("0.01")


def test_pi_parse_source_preserves_phase_partial_json_details_and_event_result_ids(
    tmp_path,
) -> None:
    session_path = tmp_path / "pi-rich-records.jsonl"
    records = [
        {
            "type": "session",
            "id": "pi-session-rich",
            "timestamp": "2026-05-07T10:00:00.000Z",
        },
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "provider": "openai-codex",
                "model": "gpt-5.4",
                "usage": {"cost": {"total": 0.02}},
                "content": [
                    {
                        "type": "text",
                        "text": "Done.",
                        "signature": {"metadata": {"phase": "final_answer"}},
                    },
                    {
                        "type": "toolCall",
                        "id": "call-read-1",
                        "name": "read",
                        "partialJson": '{"path":"README.md","limit":20}',
                    },
                ],
            },
        },
        {
            "type": "message",
            "id": "tool-result-1a",
            "parentId": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:02.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-read-1",
                "toolName": "read",
                "details": {"output": "first output"},
            },
        },
        {
            "type": "message",
            "id": "tool-result-1b",
            "parentId": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:03.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-read-1",
                "toolName": "read",
                "details": {"message": "second output"},
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    bundle = PiAdapter().parse_source(source_for_fixture(session_path))

    assistant_message = bundle.messages[0]
    assert assistant_message.metadata["phase"] == "final_answer"
    assert bundle.tool_calls[0].arguments_hash is not None
    assert bundle.tool_calls[0].metadata["partial_json_parseable"] is True
    assert bundle.tool_calls[0].metadata["path"] == "README.md"
    assert [(activity.path, activity.operation) for activity in bundle.file_activities] == [
        ("README.md", "read")
    ]
    assert len({result.tool_result_id for result in bundle.tool_results}) == 2
    assert all(result.output_hash is not None for result in bundle.tool_results)
    assert [result.output_length for result in bundle.tool_results] == [12, 13]
    assert bundle.model_usage[0].cost == Decimal("0.02")


def test_pi_parse_source_allows_repeated_file_activity_in_one_message(tmp_path) -> None:
    session_path = tmp_path / "repeated-file-activity.jsonl"
    records = [
        {
            "type": "session",
            "id": "pi-session-repeated-file",
            "timestamp": "2026-05-07T10:00:00.000Z",
            "cwd": "/tmp/session-doctor",
        },
        {
            "type": "message",
            "id": "assistant-message-1",
            "parentId": "pi-session-repeated-file",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "timestamp": "2026-05-07T10:00:01.000Z",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-read-1",
                        "name": "read",
                        "arguments": {"path": "README.md"},
                    },
                    {
                        "type": "toolCall",
                        "id": "call-read-2",
                        "name": "read",
                        "arguments": {"path": "README.md"},
                    },
                ],
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    bundle = PiAdapter().parse_source(source_for_fixture(session_path))

    assert len(bundle.file_activities) == 2
    assert len({activity.file_activity_id for activity in bundle.file_activities}) == 2


def test_pi_parse_source_counts_metadata_only_rows_without_warnings() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_source(source_for_fixture(fixture_path))

    assert bundle.session is not None
    assert bundle.session.metadata["pi_metadata_only_counts"] == {
        "branch_summary": 1,
        "compaction": 1,
        "custom": 1,
        "custom_message": 1,
        "label": 1,
        "model_change": 1,
        "session_info": 1,
        "thinking_level_change": 1,
    }


def test_pi_parse_source_emits_warnings_without_stopping() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_source(source_for_fixture(fixture_path))

    warning_codes = {warning.metadata["code"] for warning in bundle.parse_warnings}
    assert warning_codes == {
        "malformed_json",
        "unsupported_message_role",
        "unsupported_record_type",
    }
    assert len(bundle.parse_warnings) == 3
    assert len(bundle.messages) == 4

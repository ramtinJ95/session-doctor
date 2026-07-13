from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from session_doctor.adapters import SourceReadError
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


def write_jsonl(path: Path, records: Iterable[Any]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")


def session_record(
    session_id: str,
    timestamp: str = "2026-05-07T10:00:00.000Z",
    cwd: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"type": "session", "id": session_id, "timestamp": timestamp}
    if cwd is not None:
        record["cwd"] = cwd
    return record


def test_pi_parse_source_normalizes_session_raw_events_and_messages() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_live_source(source_for_fixture(fixture_path))

    assert bundle.session is not None
    assert bundle.session.native_session_id == "pi-session-1"
    assert bundle.session.cwd == "/tmp/session-doctor"
    assert bundle.session.model_provider == "openai-codex"
    assert bundle.session.model == "gpt-5.4"
    assert len(bundle.raw_events) == 15
    assert [(message.role, message.text) for message in bundle.messages] == [
        (NormalizedRole.USER, "Please fix the failing pytest in tests/test_cli.py"),
        (NormalizedRole.ASSISTANT, "I will inspect the failing test."),
        (NormalizedRole.TOOL, None),
        (NormalizedRole.TOOL, None),
    ]


def test_pi_parse_source_preserves_message_metadata_and_block_types() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_live_source(source_for_fixture(fixture_path))

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


def test_pi_parse_source_raises_for_unreadable_source(tmp_path) -> None:
    missing_path = tmp_path / "missing.jsonl"

    with pytest.raises(SourceReadError, match="Unable to read Pi source"):
        PiAdapter().parse_live_source(source_for_fixture(missing_path))


def test_pi_parse_source_warns_when_session_record_is_missing(tmp_path) -> None:
    session_path = tmp_path / "2026-05-07T10-00-00-000Z_filename-session.jsonl"
    records = [
        {
            "type": "message",
            "id": "user-message-1",
            "timestamp": "2026-05-07T10:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
            },
        }
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert bundle.session is not None
    assert bundle.session.native_session_id == "filename-session"
    warning_codes = {warning.metadata["code"] for warning in bundle.parse_warnings}
    assert "missing_session_record" in warning_codes


def test_pi_parse_source_does_not_use_source_folder_as_session_cwd(tmp_path) -> None:
    session_dir = tmp_path / "--Users-foo-workspace-my-project--"
    session_dir.mkdir()
    session_path = session_dir / "2026-05-07T10-00-00-000Z_filename-session.jsonl"
    records = [session_record("pi-session-no-cwd")]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert bundle.session is not None
    assert bundle.session.cwd is None
    assert bundle.session.project_path is None
    assert bundle.session.metadata["source_path_project_hint"] == "/Users/foo/workspace/my/project"


def test_pi_parse_source_normalizes_tools_commands_files_and_usage() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    assert bundle.command_runs[0].command_display == "pytest tests/test_cli.py -q"
    assert bundle.command_runs[0].command_normalization == "unchanged"
    assert bundle.command_runs[0].metadata["source"] == "bashExecution"
    assert bundle.command_runs[0].tool_call_id == bundle.tool_calls[0].tool_call_id

    assert [(activity.path, activity.operation) for activity in bundle.file_activities] == [
        ("tests/test_cli.py", "read"),
        ("tests/test_cli.py", "update"),
        ("scratch/output.txt", "write"),
    ]
    assert all(
        activity.canonical_path == f"/tmp/session-doctor/{activity.normalized_path}"
        for activity in bundle.file_activities
    )
    assert [activity.project_relative_path for activity in bundle.file_activities] == [
        "tests/test_cli.py",
        "tests/test_cli.py",
        "scratch/output.txt",
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
        session_record("pi-session-rich"),
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
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

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


def test_pi_parse_source_preserves_observed_non_file_tool_calls(tmp_path) -> None:
    tool_names = [
        "webfetch",
        "websearch",
        "todo",
        "subagent",
        "deep_research",
        "deep_research_lite",
    ]
    session_path = tmp_path / "pi-observed-tools.jsonl"
    records = [
        session_record("pi-session-observed-tools"),
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": f"call-{tool_name}",
                        "name": tool_name,
                        "arguments": {"query": f"{tool_name} evidence"},
                    }
                    for tool_name in tool_names
                ],
            },
        },
        *[
            {
                "type": "message",
                "id": f"tool-result-{tool_name}",
                "parentId": "assistant-message-1",
                "timestamp": "2026-05-07T10:00:02.000Z",
                "message": {
                    "role": "toolResult",
                    "toolCallId": f"call-{tool_name}",
                    "toolName": tool_name,
                    "content": [{"type": "text", "text": f"{tool_name} result"}],
                },
            }
            for tool_name in tool_names
        ],
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert [tool_call.name for tool_call in bundle.tool_calls] == tool_names
    assert all(tool_call.arguments_hash is not None for tool_call in bundle.tool_calls)
    assert len(bundle.tool_results) == len(tool_names)
    assert [result.tool_call_id for result in bundle.tool_results] == [
        tool_call.tool_call_id for tool_call in bundle.tool_calls
    ]
    assert all(result.output_hash is not None for result in bundle.tool_results)
    assert bundle.command_runs == []
    assert bundle.file_activities == []


def test_pi_parse_source_normalizes_exec_command_tool_result(tmp_path) -> None:
    session_path = tmp_path / "pi-exec-command.jsonl"
    records = [
        session_record("pi-session-exec-command"),
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-exec-1",
                        "name": "exec_command",
                        "arguments": {"cmd": "pytest -q", "workdir": "/tmp/project"},
                    }
                ],
            },
        },
        {
            "type": "message",
            "id": "tool-result-exec-1",
            "parentId": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:02.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-exec-1",
                "toolName": "exec_command",
                "isError": False,
                "details": {"exit_code": 1, "output": "failed"},
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    command_values = [
        (command.command, command.cwd, command.exit_code) for command in bundle.command_runs
    ]
    assert command_values == [("pytest -q", "/tmp/project", 1)]
    assert bundle.tool_results[0].is_error is True
    assert bundle.command_runs[0].stdout_hash is not None
    assert bundle.command_runs[0].output_length == len("failed")


def test_pi_parse_source_marks_tool_result_failed_from_structured_status(tmp_path) -> None:
    session_path = tmp_path / "pi-tool-result-status-failed.jsonl"
    records = [
        session_record("pi-session-tool-status"),
        {
            "type": "message",
            "id": "tool-result-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-1",
                "toolName": "webfetch",
                "isError": False,
                "details": {"metadata": {"status": "failed"}, "output": "request failed"},
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert bundle.tool_results[0].is_error is True


@pytest.mark.parametrize(
    ("details", "case_id"),
    [
        ({"metadata": {"outcome": "Failure"}}, "outcome"),
        ({"metadata": {"state": "timed-out"}}, "state"),
        ({"metadata": {"success": False}}, "success-false"),
        ({"metadata": {"errorMessage": "request failed"}}, "error-message"),
        ({"events": [{"status": "cancelled"}]}, "list-status"),
    ],
)
def test_pi_parse_source_marks_tool_result_failed_from_recursive_details(
    tmp_path,
    details: dict[str, object],
    case_id: str,
) -> None:
    session_path = tmp_path / f"pi-tool-result-{case_id}.jsonl"
    records = [
        session_record(f"pi-session-tool-{case_id}"),
        {
            "type": "message",
            "id": "tool-result-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-1",
                "toolName": "webfetch",
                "details": {"output": "request failed", **details},
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert bundle.tool_results[0].is_error is True


@pytest.mark.parametrize(
    ("details", "case_id"),
    [
        ({"metadata": {"status": "success"}}, "success-status"),
        ({"metadata": {"ok": True}}, "ok-true"),
        ({"metadata": {"success": True}}, "success-true"),
        ({"events": [{"status": "completed"}]}, "completed-event"),
    ],
)
def test_pi_parse_source_does_not_mark_successful_tool_details_failed(
    tmp_path,
    details: dict[str, object],
    case_id: str,
) -> None:
    session_path = tmp_path / f"pi-tool-result-success-{case_id}.jsonl"
    records = [
        session_record(f"pi-session-tool-success-{case_id}"),
        {
            "type": "message",
            "id": "tool-result-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-1",
                "toolName": "webfetch",
                "isError": False,
                "details": {"output": "request succeeded", **details},
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert bundle.tool_results[0].is_error is False


def test_pi_parse_source_dedupes_non_adjacent_bash_execution(tmp_path) -> None:
    session_path = tmp_path / "pi-bash-non-adjacent.jsonl"
    records = [
        session_record("pi-session-bash-non-adjacent"),
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-bash-1",
                        "name": "bash",
                        "arguments": {"command": "pytest -q"},
                    }
                ],
            },
        },
        {
            "type": "message",
            "id": "tool-result-bash-1",
            "parentId": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:02.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-bash-1",
                "toolName": "bash",
                "isError": True,
                "content": [{"type": "text", "text": "failed"}],
            },
        },
        {
            "type": "custom",
            "id": "custom-between",
            "parentId": "tool-result-bash-1",
            "timestamp": "2026-05-07T10:00:03.000Z",
            "data": {"kind": "between"},
        },
        {
            "type": "message",
            "id": "bash-execution-1",
            "parentId": "tool-result-bash-1",
            "timestamp": "2026-05-07T10:00:04.000Z",
            "message": {
                "role": "bashExecution",
                "command": "pytest -q",
                "exitCode": 1,
                "output": "failed",
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert [(command.command, command.exit_code) for command in bundle.command_runs] == [
        ("pytest -q", 1)
    ]
    assert bundle.command_runs[0].metadata["source"] == "bashExecution"


def test_pi_parse_source_preserves_idless_tool_calls_without_collisions(tmp_path) -> None:
    session_path = tmp_path / "pi-idless-tool-calls.jsonl"
    records = [
        session_record("pi-session-idless-tool-calls"),
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "toolCall", "name": "websearch", "arguments": {"query": "first"}},
                    {"type": "toolCall", "name": "websearch", "arguments": {"query": "second"}},
                ],
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert len(bundle.tool_calls) == 2
    assert len({tool_call.tool_call_id for tool_call in bundle.tool_calls}) == 2


def test_pi_parse_source_normalizes_apply_patch_file_activity_and_result_hash(
    tmp_path,
) -> None:
    session_path = tmp_path / "pi-apply-patch.jsonl"
    patch = """*** Begin Patch
*** Update File: src/example.py
@@
-old
+new
*** Add File: scratch/new.txt
+created
*** End Patch
"""
    records = [
        session_record("pi-session-apply-patch"),
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-apply-patch-1",
                        "name": "apply_patch",
                        "arguments": {"input": patch},
                    }
                ],
            },
        },
        {
            "type": "message",
            "id": "tool-result-apply-patch-1",
            "parentId": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:02.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "call-apply-patch-1",
                "toolName": "apply_patch",
                "details": {"diff": patch, "result": "Success."},
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert [(activity.path, activity.operation) for activity in bundle.file_activities] == [
        ("src/example.py", "update"),
        ("scratch/new.txt", "write"),
    ]
    assert all(activity.content_hash is not None for activity in bundle.file_activities)
    assert bundle.tool_results[0].output_hash is not None
    assert bundle.tool_results[0].output_length is not None
    assert bundle.tool_results[0].output_length > len("Success.")


def test_pi_parse_source_hashes_top_level_edit_text_lengths(tmp_path) -> None:
    session_path = tmp_path / "pi-edit-old-new-text.jsonl"
    records = [
        session_record("pi-session-edit-old-new-text"),
        {
            "type": "message",
            "id": "assistant-message-1",
            "timestamp": "2026-05-07T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-edit-1",
                        "name": "edit",
                        "arguments": {
                            "path": "src/example.py",
                            "oldText": "old text",
                            "newText": "new text with more content",
                        },
                    }
                ],
            },
        },
    ]
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert [(activity.path, activity.operation) for activity in bundle.file_activities] == [
        ("src/example.py", "update")
    ]
    assert bundle.file_activities[0].content_hash is not None
    assert bundle.file_activities[0].metadata["content_length"] > 0


def test_pi_parse_source_allows_repeated_file_activity_in_one_message(tmp_path) -> None:
    session_path = tmp_path / "repeated-file-activity.jsonl"
    records = [
        session_record("pi-session-repeated-file", cwd="/tmp/session-doctor"),
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
    write_jsonl(session_path, records)

    bundle = PiAdapter().parse_live_source(source_for_fixture(session_path))

    assert len(bundle.file_activities) == 2
    assert len({activity.file_activity_id for activity in bundle.file_activities}) == 2


def test_pi_parse_source_counts_metadata_only_rows_without_warnings() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = PiAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    bundle = PiAdapter().parse_live_source(source_for_fixture(fixture_path))

    warning_codes = {warning.metadata["code"] for warning in bundle.parse_warnings}
    assert warning_codes == {
        "malformed_json",
        "unsupported_message_role",
        "unsupported_record_type",
    }
    assert len(bundle.parse_warnings) == 3
    assert len(bundle.messages) == 4


def test_pi_facade_preserves_helper_imports() -> None:
    from session_doctor.adapters import pi
    from session_doctor.adapters.pi import phase_from_metadata

    assert phase_from_metadata({"metadata": {"phase": "plan"}}) == "plan"
    assert "phase_from_metadata" in pi.__all__
    assert "arguments_from_tool_call_block" in pi.__all__

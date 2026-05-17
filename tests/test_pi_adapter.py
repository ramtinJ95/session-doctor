from __future__ import annotations

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
        ("pytest tests/test_cli.py -q", 1),
    ]
    assert {command.metadata["source"] for command in bundle.command_runs} == {
        "bashExecution",
        "toolResult",
    }

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

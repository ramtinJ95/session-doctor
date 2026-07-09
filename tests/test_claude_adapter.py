from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_doctor.adapters import SourceFormatError
from session_doctor.adapters.claude import ClaudeCodeAdapter, classify_claude_path
from session_doctor.cli_options import sources_for_ingest
from session_doctor.ids import source_id_for_path
from session_doctor.privacy import hash_text
from session_doctor.schemas import AgentName, SessionSource, SourceKind

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claude"


def source_for_fixture(
    path: Path,
    *,
    source_kind: SourceKind = SourceKind.ROOT_SESSION,
) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CLAUDE, path),
        agent_name=AgentName.CLAUDE,
        source_path=str(path),
        source_kind=source_kind,
    )


def test_claude_parse_source_normalizes_root_session_end_to_end() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = ClaudeCodeAdapter().parse_source(source_for_fixture(fixture_path))

    assert bundle.session is not None
    assert bundle.session.agent_name is AgentName.CLAUDE
    assert bundle.session.native_session_id == "claude-root-1"
    assert bundle.session.cwd == "/tmp/session-doctor"
    assert bundle.session.project_path == "/tmp/session-doctor"
    assert bundle.session.agent_version == "1.1.0"
    assert bundle.session.model == "claude-sonnet-test-2"
    assert bundle.session.model_provider == "anthropic-test"
    assert bundle.session.metadata["observed_cwds"] == [
        "/tmp/session-doctor",
        "/tmp/session-doctor/packages/app",
    ]
    assert bundle.session.metadata["cwd_change_count"] == 1
    assert bundle.session.metadata["claude_versions"] == ["1.0.0", "1.1.0"]
    assert bundle.session.metadata["version_change_count"] == 1
    assert bundle.session.metadata["claude_metadata_only_counts"] == {
        "system.api_error": 1,
        "attachment": 1,
    }

    assert len(bundle.raw_events) == 9
    assert len(bundle.messages) == 6
    assert len(bundle.tool_calls) == 5
    assert len(bundle.tool_results) == 2
    assert len(bundle.command_runs) == 1
    assert len(bundle.file_activities) == 3
    assert len(bundle.model_usage) == 2
    assert {warning.metadata["code"] for warning in bundle.parse_warnings} == {
        "claude_api_error",
        "unsupported_content_shape",
        "unsupported_record_type",
    }


def test_claude_messages_exclude_thinking_and_tool_result_text() -> None:
    bundle = ClaudeCodeAdapter().parse_source(
        source_for_fixture(FIXTURE_DIR / "basic-session.jsonl")
    )

    assistant = next(
        message for message in bundle.messages if message.native_message_id == "assistant-1"
    )
    command_result_message = next(
        message for message in bundle.messages if message.native_message_id == "user-2"
    )
    mixed_result_message = next(
        message for message in bundle.messages if message.native_message_id == "user-3"
    )
    system_message = next(
        message for message in bundle.messages if message.native_message_id == "system-2"
    )

    assert assistant.text == "I will inspect the project."
    assert assistant.content_block_types == ["text", "thinking", "tool_use"]
    assert assistant.metadata["thinking_block_count"] == 1
    assert command_result_message.text is None
    assert command_result_message.content_block_types == ["tool_result"]
    assert mixed_result_message.text == "Please continue."
    assert mixed_result_message.content_block_types == ["tool_result", "text"]
    assert system_message.text == "Synthetic system notice."


def test_claude_normalizes_tools_commands_files_and_usage_without_raw_output() -> None:
    bundle = ClaudeCodeAdapter().parse_source(
        source_for_fixture(FIXTURE_DIR / "basic-session.jsonl")
    )

    bash_call = next(call for call in bundle.tool_calls if call.name == "Bash")
    assert bash_call.native_tool_call_id == "tool-bash-1"
    assert bash_call.arguments_hash is not None
    assert bash_call.metadata["argument_keys"] == ["command", "description", "timeout"]
    assert bash_call.metadata["command_length"] > 0

    failed_result = next(
        result for result in bundle.tool_results if result.native_tool_call_id == "tool-bash-1"
    )
    unknown_result = next(
        result for result in bundle.tool_results if result.native_tool_call_id == "tool-read-1"
    )
    assert failed_result.is_error is True
    assert failed_result.output_hash == hash_text("PRIVATE_COMMAND_OUTPUT")
    assert failed_result.output_length == len("PRIVATE_COMMAND_OUTPUT")
    assert unknown_result.is_error is None

    command = bundle.command_runs[0]
    assert command.command == "/bin/zsh -lc 'pytest -q'"
    assert command.command_display == "pytest -q"
    assert command.command_normalization == "shell_wrapper:zsh:-lc"
    assert command.cwd == "/tmp/session-doctor"
    assert command.exit_code == 1
    assert command.stdout_hash == hash_text("PRIVATE_STDOUT")
    assert command.stderr_hash == hash_text("PRIVATE_STDERR")
    assert command.output_length == len("PRIVATE_STDOUT") + len("PRIVATE_STDERR")
    assert command.metadata["interrupted"] is False

    activities = {activity.path: activity for activity in bundle.file_activities}
    assert activities["../../README.md"].canonical_path == "/tmp/session-doctor/README.md"
    assert activities["../../README.md"].project_relative_path == "README.md"
    assert activities["../../README.md"].operation == "read"
    assert activities["src/app.py"].operation == "update"
    assert activities["src/app.py"].content_hash is not None
    assert activities["notes.txt"].operation == "write"
    assert activities["notes.txt"].content_hash is not None

    first_usage, second_usage = bundle.model_usage
    assert first_usage.input_tokens == 100
    assert first_usage.output_tokens == 20
    assert first_usage.cache_read_tokens == 30
    assert first_usage.cache_write_tokens == 10
    assert first_usage.total_tokens == 160
    assert first_usage.cost is None
    assert first_usage.metadata["unmapped_usage_keys"] == ["future_counter", "service_tier"]
    assert second_usage.total_tokens == 75


def test_claude_bundle_never_contains_private_structural_content() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    bundle = ClaudeCodeAdapter().parse_source(source_for_fixture(fixture_path))
    serialized_bundle = bundle.model_dump_json()

    forbidden_values = {
        "PRIVATE_THINKING_TEXT",
        "PRIVATE_COMMAND_OUTPUT",
        "PRIVATE_STDOUT",
        "PRIVATE_STDERR",
        "PRIVATE_OLD_EDIT_BODY",
        "PRIVATE_NEW_EDIT_BODY",
        "PRIVATE_WRITE_BODY",
        "PRIVATE_AGENT_ARGUMENT",
        "PRIVATE_UNSUPPORTED_BLOCK",
        "PRIVATE_READ_OUTPUT",
        "PRIVATE_ORIGINAL_FILE",
        "PRIVATE_PATCH",
        "PRIVATE_API_ERROR",
        "PRIVATE_UNKNOWN_RECORD",
    }
    assert all(value not in serialized_bundle for value in forbidden_values)


def test_claude_missing_session_id_uses_filename_and_preserves_drift() -> None:
    fixture_path = FIXTURE_DIR / "drift-and-warnings.jsonl"
    bundle = ClaudeCodeAdapter().parse_source(source_for_fixture(fixture_path))

    assert bundle.session is not None
    assert bundle.session.native_session_id is None
    assert bundle.session.cwd == "/tmp/earlier-cwd"
    assert bundle.session.agent_version == "2.0.0"
    assert bundle.session.metadata["observed_cwds"] == [
        "/tmp/later-cwd",
        "/tmp/earlier-cwd",
    ]
    assert bundle.session.metadata["claude_versions"] == ["2.0.0", "1.9.0"]
    assert len(bundle.raw_events) == 4
    assert {warning.metadata["code"] for warning in bundle.parse_warnings} == {
        "missing_session_id",
        "unsupported_content_shape",
        "unsupported_message_shape",
        "unsupported_record_type",
    }


def test_claude_warns_for_malformed_rows_and_inconsistent_session_ids(tmp_path) -> None:
    source_path = tmp_path / "mixed.jsonl"
    source_path.write_text(
        "\n".join(
            (
                "{bad json",
                json.dumps(["not", "an", "object"]),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "first-id",
                        "uuid": "user-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"role": "user", "content": "Hello"},
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "second-id",
                        "uuid": "user-2",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {"role": "user", "content": "Again"},
                    }
                ),
            )
        )
    )

    bundle = ClaudeCodeAdapter().parse_source(source_for_fixture(source_path))

    assert len(bundle.raw_events) == 2
    assert len(bundle.messages) == 2
    assert {warning.metadata["code"] for warning in bundle.parse_warnings} == {
        "inconsistent_session_id",
        "malformed_json",
        "non_object_record",
    }


def test_claude_idless_blocks_get_distinct_fallback_ids_and_boolean_usage_is_rejected(
    tmp_path,
) -> None:
    source_path = tmp_path / "idless.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "uuid": "assistant-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": "one"}},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "two"}},
                        {"type": "future-one"},
                        {"type": "future-two"},
                    ],
                    "usage": {"input_tokens": True, "output_tokens": 2},
                },
            }
        )
    )

    bundle = ClaudeCodeAdapter().parse_source(source_for_fixture(source_path))

    assert len(bundle.tool_calls) == 2
    assert len({tool_call.tool_call_id for tool_call in bundle.tool_calls}) == 2
    assert len(bundle.command_runs) == 2
    assert len({command.command_run_id for command in bundle.command_runs}) == 2
    assert len({warning.warning_id for warning in bundle.parse_warnings}) == 4
    assert [warning.metadata["code"] for warning in bundle.parse_warnings].count(
        "missing_tool_use_id"
    ) == 2
    assert bundle.model_usage[0].input_tokens is None
    assert bundle.model_usage[0].output_tokens == 2
    assert bundle.model_usage[0].total_tokens == 2


def test_claude_discovery_classifies_all_sources_but_ingests_only_roots(tmp_path) -> None:
    project_dir = tmp_path / "project"
    session_dir = project_dir / "session-1"
    subagents_dir = session_dir / "subagents"
    tool_results_dir = session_dir / "tool-results"
    subagents_dir.mkdir(parents=True)
    tool_results_dir.mkdir()
    paths = {
        project_dir / "root.jsonl": SourceKind.ROOT_SESSION,
        subagents_dir / "agent-a.jsonl": SourceKind.SUBSESSION,
        subagents_dir / "agent-a.meta.json": SourceKind.SUBAGENT_METADATA,
        tool_results_dir / "result.txt": SourceKind.TOOL_RESULT,
        project_dir / "memory.md": SourceKind.MEMORY,
        project_dir / "settings.json": SourceKind.AUXILIARY,
    }
    for path in paths:
        path.touch()

    adapter = ClaudeCodeAdapter()
    discovered = adapter.discover(tmp_path)
    assert {Path(source.source_path): source.source_kind for source in discovered} == paths
    assert [source.source_kind for source in sources_for_ingest(adapter, tmp_path)] == [
        SourceKind.ROOT_SESSION
    ]
    assert classify_claude_path(subagents_dir / "agent-a.jsonl", tmp_path) is (
        SourceKind.SUBSESSION
    )


def test_claude_rejects_non_root_source_kind() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"

    with pytest.raises(SourceFormatError, match="is not parsed in PR 2"):
        ClaudeCodeAdapter().parse_source(
            source_for_fixture(fixture_path, source_kind=SourceKind.SUBSESSION)
        )

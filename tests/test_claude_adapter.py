from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from session_doctor.adapters import SourceFormatError
from session_doctor.adapters.base import CapturedAdapterMember
from session_doctor.adapters.claude import ClaudeCodeAdapter, classify_claude_path
from session_doctor.adapters.claude_sidecars import hash_sidecar
from session_doctor.analysis import analyze_features, classify_session
from session_doctor.cli_options import sources_for_ingest
from session_doctor.ids import source_id_for_path
from session_doctor.privacy import hash_text
from session_doctor.schemas import AgentName, NormalizedRole, SessionSource, SourceKind
from session_doctor.store import DuckDBStore

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claude"
TOPOLOGY_FIXTURE_DIR = FIXTURE_DIR / "topology"


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
    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(fixture_path))

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
    bundle = ClaudeCodeAdapter().parse_live_source(
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
    assert command_result_message.role is NormalizedRole.TOOL
    assert command_result_message.text is None
    assert command_result_message.content_block_types == ["tool_result"]
    assert mixed_result_message.role is NormalizedRole.USER
    assert mixed_result_message.text == "Please continue."
    assert mixed_result_message.content_block_types == ["tool_result", "text"]
    assert system_message.text == "Synthetic system notice."
    features = analyze_features(bundle, "analysis-run")
    feature_values = {
        feature.feature_name: feature.feature_value for feature in features.session_features
    }
    assert feature_values["user_message_count"] == "2"


def test_claude_end_turn_marks_final_answer_and_resolves_correction(tmp_path) -> None:
    source_path = tmp_path / "resolved.jsonl"
    source_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "session-1",
                        "uuid": "user-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {
                            "role": "user",
                            "content": "That is not what I asked. Please fix it.",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "session-1",
                        "uuid": "assistant-1",
                        "parentUuid": "user-1",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Fixed."}],
                            "stop_reason": "end_turn",
                        },
                    }
                ),
            )
        )
    )
    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

    assert bundle.messages[-1].metadata["phase"] == "final_answer"
    features = analyze_features(bundle, "analysis-run")
    feature_values = {
        feature.feature_name: feature.feature_value for feature in features.session_features
    }
    assert feature_values["unresolved_ending_signal"] == "false"
    classifications = classify_session(
        bundle,
        "analysis-run",
        features.message_features,
        features.session_features,
    )
    labels = {classification.label for classification in classifications}
    assert "resolved_after_corrections" in labels
    assert "user_stuck" not in labels


def test_claude_max_tokens_stop_is_unresolved_evidence(tmp_path) -> None:
    source_path = tmp_path / "max-tokens.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "uuid": "assistant-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Incomplete response"}],
                    "stop_reason": "max_tokens",
                    "stop_sequence": None,
                },
            }
        )
    )

    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

    warning = next(
        warning
        for warning in bundle.parse_warnings
        if warning.metadata["code"] == "claude_assistant_truncated"
    )
    assert warning.metadata["stop_reason"] == "max_tokens"
    features = analyze_features(bundle, "analysis-run")
    feature_values = {feature.feature_name: feature for feature in features.session_features}
    assert feature_values["unresolved_ending_signal"].feature_value == "true"
    assert (
        warning.warning_id
        in feature_values["unresolved_ending_signal"].evidence["late_parse_warning_ids"]
    )


def test_claude_stop_sequence_is_not_truncation_evidence(tmp_path) -> None:
    source_path = tmp_path / "stop-sequence.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "uuid": "assistant-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Configured completion"}],
                    "stop_reason": "stop_sequence",
                    "stop_sequence": "END",
                },
            }
        )
    )

    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

    assert all(
        warning.metadata["code"] != "claude_assistant_truncated"
        for warning in bundle.parse_warnings
    )
    features = analyze_features(bundle, "analysis-run")
    feature_values = {
        feature.feature_name: feature.feature_value for feature in features.session_features
    }
    assert feature_values["unresolved_ending_signal"] == "false"


def test_claude_api_error_message_text_is_hashed_but_not_persisted(tmp_path) -> None:
    source_path = tmp_path / "api-error.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "uuid": "assistant-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "isApiErrorMessage": True,
                "error": {"type": "overloaded_error"},
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "PRIVATE_API_ERROR_MESSAGE"}],
                    "stop_reason": "end_turn",
                },
            }
        )
    )
    source = source_for_fixture(source_path)
    bundle = ClaudeCodeAdapter().parse_live_source(source)

    assert bundle.messages[0].text is None
    assert bundle.messages[0].text_hash == hash_text("PRIVATE_API_ERROR_MESSAGE")
    assert bundle.messages[0].text_length == len("PRIVATE_API_ERROR_MESSAGE")
    assert bundle.messages[0].metadata["error_content_redacted"] is True
    assert "phase" not in bundle.messages[0].metadata
    assert "claude_assistant_error" in {
        warning.metadata["code"] for warning in bundle.parse_warnings
    }
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)
    loaded = store.load_session_bundle(bundle.session.session_id) if bundle.session else None
    assert loaded is not None
    assert "PRIVATE_API_ERROR_MESSAGE" not in loaded.model_dump_json()


def test_claude_local_command_output_is_hashed_but_not_persisted_as_message_text(
    tmp_path,
) -> None:
    source_path = tmp_path / "local-command.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "type": "system",
                "subtype": "local_command",
                "sessionId": "session-1",
                "uuid": "system-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "content": "PRIVATE_LOCAL_COMMAND_OUTPUT",
            }
        )
    )
    source = source_for_fixture(source_path)
    bundle = ClaudeCodeAdapter().parse_live_source(source)

    assert bundle.messages == []
    assert bundle.session is not None
    assert bundle.session.metadata["claude_metadata_only_counts"] == {"system.local_command": 1}
    assert bundle.raw_events[0].metadata["local_command_output_hash"] == hash_text(
        "PRIVATE_LOCAL_COMMAND_OUTPUT"
    )
    assert bundle.raw_events[0].metadata["local_command_output_length"] == len(
        "PRIVATE_LOCAL_COMMAND_OUTPUT"
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)
    loaded = store.load_session_bundle(bundle.session.session_id)
    assert loaded is not None
    assert loaded.messages == []
    assert "PRIVATE_LOCAL_COMMAND_OUTPUT" not in loaded.model_dump_json()


def test_claude_normalizes_tools_commands_files_and_usage_without_raw_output() -> None:
    bundle = ClaudeCodeAdapter().parse_live_source(
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
    assert activities["src/app.py"].metadata["replace_all"] is False
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
    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(fixture_path))
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
    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(fixture_path))

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

    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

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

    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

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


def test_claude_bash_error_without_exit_code_is_visible_to_analysis(tmp_path) -> None:
    source_path = tmp_path / "bash-error.jsonl"
    source_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "session-1",
                        "uuid": "assistant-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "bash-1",
                                    "name": "Bash",
                                    "input": {"command": "false"},
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "session-1",
                        "uuid": "user-1",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "bash-1",
                                    "content": "failed",
                                    "is_error": True,
                                }
                            ],
                        },
                    }
                ),
            )
        )
    )

    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

    assert bundle.command_runs[0].exit_code == 1
    assert bundle.command_runs[0].metadata["exit_code_inferred_from_is_error"] is True
    features = analyze_features(bundle, "analysis-run")
    feature_values = {
        feature.feature_name: feature.feature_value for feature in features.session_features
    }
    assert feature_values["failed_command_count"] == "1"


def test_claude_empty_bash_streams_do_not_create_repeated_failure_evidence(
    tmp_path,
) -> None:
    source_path = tmp_path / "empty-streams.jsonl"
    records: list[dict[str, object]] = []
    for index in range(3):
        tool_id = f"bash-{index}"
        records.extend(
            [
                {
                    "type": "assistant",
                    "sessionId": "session-1",
                    "uuid": f"assistant-{index}",
                    "timestamp": f"2026-01-01T00:00:0{index * 2}Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": "Bash",
                                "input": {"command": f"different-command-{index}"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "sessionId": "session-1",
                    "uuid": f"user-{index}",
                    "timestamp": f"2026-01-01T00:00:0{index * 2 + 1}Z",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "is_error": True,
                            }
                        ],
                    },
                    "toolUseResult": {"stdout": "", "stderr": "", "exitCode": 1},
                },
            ]
        )
    source_path.write_text("\n".join(json.dumps(record) for record in records))

    bundle = ClaudeCodeAdapter().parse_live_source(source_for_fixture(source_path))

    assert all(command.stdout_hash is None for command in bundle.command_runs)
    assert all(command.stderr_hash is None for command in bundle.command_runs)
    assert all(command.output_length is None for command in bundle.command_runs)
    features = analyze_features(bundle, "analysis-run")
    feature_values = {
        feature.feature_name: feature.feature_value for feature in features.session_features
    }
    assert feature_values["repeated_command_failure_count"] == "0"
    classifications = classify_session(
        bundle,
        "analysis-run",
        features.message_features,
        features.session_features,
    )
    assert "agent_looping" not in {classification.label for classification in classifications}


def test_claude_idless_duplicate_file_tools_persist_with_explicit_false(tmp_path) -> None:
    source_path = tmp_path / "idless-files.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "uuid": "assistant-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "cwd": "/tmp/project",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {
                                "file_path": "same.py",
                                "old_string": "one",
                                "new_string": "two",
                                "replace_all": False,
                            },
                        },
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {
                                "file_path": "same.py",
                                "old_string": "two",
                                "new_string": "three",
                                "replace_all": False,
                            },
                        },
                    ],
                },
            }
        )
    )
    source = source_for_fixture(source_path)
    bundle = ClaudeCodeAdapter().parse_live_source(source)

    assert len(bundle.file_activities) == 2
    assert len({activity.file_activity_id for activity in bundle.file_activities}) == 2
    assert all(activity.metadata["replace_all"] is False for activity in bundle.file_activities)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)
    assert store.table_count("file_activities") == 2


def test_claude_discovery_classifies_all_sources_and_ingests_transcripts(tmp_path) -> None:
    project_dir = tmp_path / "project"
    session_dir = project_dir / "session-1"
    subagents_dir = session_dir / "subagents"
    tool_results_dir = session_dir / "tool-results"
    subagents_dir.mkdir(parents=True)
    tool_results_dir.mkdir()
    paths = {
        project_dir / "session-1.jsonl": SourceKind.ROOT_SESSION,
        subagents_dir / "agent-a.jsonl": SourceKind.SUBSESSION,
        subagents_dir / "agent-a.meta.json": SourceKind.SUBAGENT_METADATA,
        tool_results_dir / "result.txt": SourceKind.TOOL_RESULT,
        tool_results_dir / "result.jsonl": SourceKind.TOOL_RESULT,
        project_dir / "memory.md": SourceKind.MEMORY,
        project_dir / "settings.json": SourceKind.AUXILIARY,
    }
    for path in paths:
        path.touch()

    adapter = ClaudeCodeAdapter()
    discovered = adapter.discover(tmp_path)
    assert {Path(source.source_path): source.source_kind for source in discovered} == paths
    assert [source.source_kind for source in sources_for_ingest(adapter, tmp_path)] == [
        SourceKind.ROOT_SESSION,
        SourceKind.SUBSESSION,
    ]
    assert classify_claude_path(subagents_dir / "agent-a.jsonl", tmp_path) is (
        SourceKind.SUBSESSION
    )
    assert [source.source_kind for source in sources_for_ingest(adapter, project_dir)] == [
        SourceKind.ROOT_SESSION,
        SourceKind.SUBSESSION,
    ]
    assert [source.source_kind for source in sources_for_ingest(adapter, session_dir)] == [
        SourceKind.SUBSESSION
    ]
    assert [source.source_kind for source in sources_for_ingest(adapter, subagents_dir)] == [
        SourceKind.SUBSESSION
    ]
    assert sources_for_ingest(adapter, tool_results_dir) == []


def test_claude_explicit_file_classification_matches_discovery(tmp_path) -> None:
    project_named_tool_results = tmp_path / "tool-results"
    project_named_tool_results.mkdir()
    root_path = project_named_tool_results / "session.jsonl"
    root_path.write_text("{}\n")
    adapter = ClaudeCodeAdapter()

    discovered = adapter.discover(tmp_path)
    discovered_kind = next(
        source.source_kind for source in discovered if source.source_path == str(root_path)
    )
    explicit_kind = adapter.source_for_path(root_path).source_kind

    assert discovered_kind is SourceKind.ROOT_SESSION
    assert explicit_kind is SourceKind.ROOT_SESSION

    native_project = tmp_path / "project"
    session_dir = native_project / "session-1"
    sidecar_dir = session_dir / "tool-results"
    sidecar_dir.mkdir(parents=True)
    (native_project / "session-1.jsonl").write_text("{}\n")
    sidecar_path = sidecar_dir / "result.jsonl"
    sidecar_path.write_text("{}\n")
    assert adapter.source_for_path(sidecar_path).source_kind is SourceKind.TOOL_RESULT


def test_claude_rejects_non_transcript_source_kind() -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"

    with pytest.raises(SourceFormatError, match="is not a session transcript"):
        ClaudeCodeAdapter().parse_live_source(
            source_for_fixture(fixture_path, source_kind=SourceKind.TOOL_RESULT)
        )


def test_claude_topology_links_root_and_nested_subagents() -> None:
    adapter = ClaudeCodeAdapter()
    selected = sources_for_ingest(adapter, TOPOLOGY_FIXTURE_DIR)
    root_source, agent_a_source, agent_b_source = selected

    assert [source.source_kind for source in selected] == [
        SourceKind.ROOT_SESSION,
        SourceKind.SUBSESSION,
        SourceKind.SUBSESSION,
    ]
    assert agent_a_source.parent_source_id == root_source.source_id
    assert agent_b_source.parent_source_id == agent_a_source.source_id

    root_bundle = adapter.parse_live_source(root_source)
    agent_a_bundle = adapter.parse_live_source(agent_a_source)
    agent_b_bundle = adapter.parse_live_source(agent_b_source)

    assert root_bundle.session is not None
    assert agent_a_bundle.session is not None
    assert agent_b_bundle.session is not None
    assert root_bundle.session.is_sidechain is False
    assert agent_a_bundle.session.is_sidechain is True
    assert agent_b_bundle.session.is_sidechain is True
    assert agent_a_bundle.session.parent_session_id == root_bundle.session.session_id
    assert agent_b_bundle.session.parent_session_id == agent_a_bundle.session.session_id
    assert agent_a_bundle.session.metadata["nesting_depth"] == 1
    assert agent_b_bundle.session.metadata["nesting_depth"] == 2
    assert agent_a_bundle.session.metadata["agent_ids"] == ["agent-a"]
    assert agent_a_bundle.session.metadata["subagent_metadata"]["agent_type"] == "Explore"
    assert agent_b_bundle.session.metadata["subagent_metadata"]["permission_mode"] == "plan"

    root_warning_codes = {warning.metadata["code"] for warning in root_bundle.parse_warnings}
    assert "orphan_subagent_metadata" in root_warning_codes
    assert "orphan_tool_result_sidecar" in root_warning_codes

    directly_selected = sources_for_ingest(
        adapter,
        TOPOLOGY_FIXTURE_DIR / "project/session-root/subagents",
    )
    assert len(directly_selected) == 2
    assert all(source.parent_source_id is not None for source in directly_selected)


def test_claude_relative_discovery_does_not_mark_referenced_sidecar_orphaned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    relative_root = Path("claude-topology")
    topology_root = tmp_path / relative_root
    project_dir = topology_root / "project"
    tool_results_dir = project_dir / "session-1" / "tool-results"
    tool_results_dir.mkdir(parents=True)
    (tool_results_dir / "referenced.txt").write_text("output")
    (tool_results_dir / "orphan.txt").write_text("orphan")
    (project_dir / "session-1.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "session-1",
                "uuid": "result-1",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tool-1"}],
                },
                "toolUseResult": {
                    "persistedOutputPath": "tool-results/referenced.txt",
                },
            }
        )
    )
    monkeypatch.chdir(tmp_path)

    root_source = next(
        source
        for source in ClaudeCodeAdapter().discover(relative_root)
        if source.source_kind is SourceKind.ROOT_SESSION
    )

    assert root_source.metadata["claude_orphan_tool_result_count"] == 1


def test_claude_parent_session_id_uses_first_observed_native_id(tmp_path) -> None:
    project_dir = tmp_path / "project"
    session_dir = project_dir / "session-1"
    subagents_dir = session_dir / "subagents"
    subagents_dir.mkdir(parents=True)
    root_path = project_dir / "session-1.jsonl"
    root_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        **assistant_agent_record("root-parent", "agent-tool", sidechain=False),
                        "sessionId": "z-first",
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "a-second",
                        "uuid": "root-user",
                        "message": {"role": "user", "content": "continue"},
                    }
                ),
            )
        )
    )
    child_path = subagents_dir / "agent-child.jsonl"
    child_path.write_text(
        json.dumps(assistant_text_record("child", sidechain=True, agent_id="child"))
    )
    child_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "Explore", "toolUseId": "agent-tool"})
    )
    adapter = ClaudeCodeAdapter()
    sources = {Path(source.source_path): source for source in adapter.discover(tmp_path)}

    root_bundle = adapter.parse_live_source(sources[root_path])
    child_bundle = adapter.parse_live_source(sources[child_path])

    assert root_bundle.session is not None
    assert child_bundle.session is not None
    assert root_bundle.session.native_session_id == "z-first"
    assert child_bundle.session.parent_session_id == root_bundle.session.session_id


def test_claude_cyclic_subagent_links_are_invalidated(tmp_path) -> None:
    project_dir = tmp_path / "project"
    subagents_dir = project_dir / "session-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (project_dir / "session-1.jsonl").write_text(
        json.dumps(assistant_text_record("root", sidechain=False, agent_id="root"))
    )
    agent_a_path = subagents_dir / "agent-a.jsonl"
    agent_b_path = subagents_dir / "agent-b.jsonl"
    agent_a_path.write_text(json.dumps(assistant_agent_record("agent-a", "tool-a", sidechain=True)))
    agent_b_path.write_text(json.dumps(assistant_agent_record("agent-b", "tool-b", sidechain=True)))
    agent_a_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "Explore", "toolUseId": "tool-b"})
    )
    agent_b_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "Explore", "toolUseId": "tool-a"})
    )
    adapter = ClaudeCodeAdapter()
    subagents = [
        source
        for source in adapter.discover(tmp_path)
        if source.source_kind is SourceKind.SUBSESSION
    ]

    for source in subagents:
        bundle = adapter.parse_live_source(source)
        assert source.parent_source_id is None
        assert source.metadata["claude_parent_link_status"] == "cyclic"
        assert "claude_parent_session_id" not in source.metadata
        assert bundle.session is not None
        assert bundle.session.parent_session_id is None
        assert "subagent_parent_cyclic" in {
            warning.metadata["code"] for warning in bundle.parse_warnings
        }


def test_claude_explicit_subagent_discovers_only_its_session_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter()
    subagent_path = TOPOLOGY_FIXTURE_DIR / "project/session-root/subagents/agent-a.jsonl"
    discovered_roots: list[Path | None] = []
    original_discover = adapter.discover

    def tracked_discover(root: Path | None = None) -> list[SessionSource]:
        discovered_roots.append(root)
        return original_discover(root)

    monkeypatch.setattr(adapter, "discover", tracked_discover)

    source = adapter.source_for_path(subagent_path)

    assert source.source_kind is SourceKind.SUBSESSION
    assert discovered_roots == [subagent_path.parent.parent]


def test_claude_correlates_tool_result_sidecar_without_raw_content() -> None:
    adapter = ClaudeCodeAdapter()
    root_source = sources_for_ingest(adapter, TOPOLOGY_FIXTURE_DIR)[0]

    bundle = adapter.parse_live_source(root_source)

    tool_result = bundle.tool_results[0]
    expected_output = (
        TOPOLOGY_FIXTURE_DIR / "project/session-root/tool-results/result-a.txt"
    ).read_bytes()
    assert tool_result.output_hash == hash_text(expected_output.decode())
    assert tool_result.output_length == len(expected_output)
    assert tool_result.metadata["sidecar_correlated"] is True
    assert tool_result.metadata["sidecar_byte_length"] == len(expected_output)
    assert tool_result.metadata["sidecar_character_length"] == len(expected_output.decode())
    serialized = bundle.model_dump_json()
    assert "PRIVATE_PERSISTED_TOOL_OUTPUT" not in serialized
    assert "PRIVATE_ORPHAN_TOOL_OUTPUT" not in serialized
    assert "PRIVATE_SUBAGENT_TASK" not in serialized


def test_claude_sidecar_lengths_use_text_characters_for_unicode_output(tmp_path) -> None:
    root_path = tmp_path / "session-1.jsonl"
    tool_results_dir = tmp_path / "session-1" / "tool-results"
    tool_results_dir.mkdir(parents=True)
    output = "é🙂"
    encoded_output = output.encode()
    (tool_results_dir / "inline.txt").write_bytes(encoded_output)
    (tool_results_dir / "persisted.txt").write_bytes(encoded_output)
    records = [
        {
            "type": "user",
            "sessionId": "session-1",
            "uuid": "inline-result",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "inline-tool", "content": output}
                ],
            },
            "toolUseResult": {"persistedOutputPath": "tool-results/inline.txt"},
        },
        {
            "type": "user",
            "sessionId": "session-1",
            "uuid": "persisted-result",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "persisted-tool"}],
            },
            "toolUseResult": {"persistedOutputPath": "tool-results/persisted.txt"},
        },
    ]
    root_path.write_text("\n".join(json.dumps(record) for record in records))

    bundle = ClaudeCodeAdapter().parse_live_source(ClaudeCodeAdapter().source_for_path(root_path))

    inline_result, persisted_result = bundle.tool_results
    assert inline_result.output_length == len(output)
    assert inline_result.metadata["inline_output_truncated"] is False
    assert inline_result.metadata["sidecar_byte_length"] == len(encoded_output)
    assert inline_result.metadata["sidecar_character_length"] == len(output)
    assert persisted_result.output_hash == hash_text(output)
    assert persisted_result.output_length == len(output)


def test_claude_does_not_apply_one_sidecar_to_multiple_tool_results(tmp_path) -> None:
    root_path = tmp_path / "session-1.jsonl"
    tool_results_dir = tmp_path / "session-1" / "tool-results"
    tool_results_dir.mkdir(parents=True)
    (tool_results_dir / "ambiguous.txt").write_text("persisted output")
    root_path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "session-1",
                "uuid": "result-1",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool-1"},
                        {"type": "tool_result", "tool_use_id": "tool-2"},
                    ],
                },
                "toolUseResult": {
                    "persistedOutputPath": "tool-results/ambiguous.txt",
                },
            }
        )
    )

    bundle = ClaudeCodeAdapter().parse_live_source(ClaudeCodeAdapter().source_for_path(root_path))

    assert len(bundle.tool_results) == 2
    assert all("sidecar_correlated" not in result.metadata for result in bundle.tool_results)
    warning = next(
        warning
        for warning in bundle.parse_warnings
        if warning.metadata["code"] == "ambiguous_tool_result_sidecar"
    )
    assert warning.metadata["tool_result_block_count"] == 2


def test_claude_sidecar_hashing_reads_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * (1024 * 1024 - 1) + "é".encode() + b"x" * (1024 * 1024)

    class GuardedBytesIO(BytesIO):
        def read(self, size: int | None = -1, /) -> bytes:
            assert size is not None and 0 < size <= 1024 * 1024
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda self, mode: GuardedBytesIO(payload))

    digest, byte_length, character_length = hash_sidecar(Path("ignored"))

    assert digest == hash_text(payload.decode())
    assert byte_length == len(payload)
    assert character_length == len(payload.decode())


def test_claude_subagent_without_parent_signal_warns_instead_of_guessing(tmp_path) -> None:
    project_dir = tmp_path / "project"
    subagents_dir = project_dir / "session-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (project_dir / "session-1.jsonl").write_text(
        '{"type":"assistant","sessionId":"session-1","uuid":"root-1"}\n'
    )
    subagent_path = subagents_dir / "agent-a.jsonl"
    subagent_path.write_text(
        '{"type":"assistant","sessionId":"session-1","uuid":"sub-1",'
        '"isSidechain":true,"message":{"role":"assistant","content":[]}}\n'
    )
    adapter = ClaudeCodeAdapter()
    subagent_source = next(
        source
        for source in adapter.discover(tmp_path)
        if source.source_kind is SourceKind.SUBSESSION
    )

    bundle = adapter.parse_live_source(subagent_source)

    assert subagent_source.parent_source_id is None
    assert bundle.session is not None
    assert bundle.session.parent_session_id is None
    assert {warning.metadata["code"] for warning in bundle.parse_warnings} >= {
        "subagent_parent_missing",
        "subagent_metadata_missing",
    }


def test_claude_subagent_ambiguous_parent_signals_remain_unlinked(tmp_path) -> None:
    project_dir = tmp_path / "project"
    subagents_dir = project_dir / "session-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (project_dir / "session-1.jsonl").write_text(
        json.dumps(assistant_agent_record("root-parent", "shared-tool", sidechain=False))
    )
    (subagents_dir / "agent-parent.jsonl").write_text(
        json.dumps(assistant_agent_record("sub-parent", "shared-tool", sidechain=True))
    )
    child_path = subagents_dir / "agent-child.jsonl"
    child_path.write_text(
        json.dumps(assistant_text_record("child", sidechain=True, agent_id="child"))
    )
    child_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "Explore", "toolUseId": "shared-tool"})
    )
    adapter = ClaudeCodeAdapter()
    child_source = next(
        source for source in adapter.discover(tmp_path) if Path(source.source_path) == child_path
    )

    bundle = adapter.parse_live_source(child_source)

    assert child_source.parent_source_id is None
    assert child_source.metadata["claude_parent_link_status"] == "ambiguous"
    assert child_source.metadata["claude_parent_candidate_count"] == 2
    assert len(child_source.metadata["claude_parent_candidate_source_ids"]) == 2
    assert "subagent_parent_ambiguous" in {
        warning.metadata["code"] for warning in bundle.parse_warnings
    }
    captured_source = adapter.source_for_captured_parse(child_source)
    captured_members = adapter.bundle_member_sources(child_source, child_path.read_bytes())
    transcript_members = [
        member
        for member, role in captured_members
        if role in {"related_transcript", "subagent_transcript"}
    ]
    assert len(transcript_members) == 2
    context = (
        CapturedAdapterMember(captured_source, "primary", child_path.read_bytes()),
        *(
            CapturedAdapterMember(member, role, Path(member.source_path).read_bytes())
            for member, role in captured_members
            if Path(member.source_path).is_file()
        ),
    )
    replayed = adapter.parse_source(
        adapter.prepare_captured_source(captured_source, context),
        child_path.read_bytes(),
    )
    assert replayed.session is not None
    assert replayed.session.parent_session_id is None
    assert "subagent_parent_ambiguous" in {
        warning.metadata["code"] for warning in replayed.parse_warnings
    }


def test_claude_subagent_conflicting_parent_signals_warn(tmp_path) -> None:
    project_dir = tmp_path / "project"
    subagents_dir = project_dir / "session-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    root_record = assistant_agent_record("root-parent", "root-tool", sidechain=False)
    root_record["agentId"] = "root-agent"
    (project_dir / "session-1.jsonl").write_text(json.dumps(root_record))
    sub_parent_record = assistant_agent_record("sub-parent", "sub-tool", sidechain=True)
    sub_parent_record["agentId"] = "sub-parent-agent"
    (subagents_dir / "agent-parent.jsonl").write_text(json.dumps(sub_parent_record))
    child_path = subagents_dir / "agent-child.jsonl"
    child_record = assistant_text_record("child", sidechain=True, agent_id="child")
    child_path.write_text(json.dumps(child_record))
    child_path.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "agentType": "Explore",
                "parentAgentId": "root-agent",
                "toolUseId": "sub-tool",
            }
        )
    )
    adapter = ClaudeCodeAdapter()
    child_source = next(
        source for source in adapter.discover(tmp_path) if Path(source.source_path) == child_path
    )

    bundle = adapter.parse_live_source(child_source)

    assert child_source.parent_source_id is None
    assert child_source.metadata["claude_parent_link_status"] == "mismatched"
    assert "subagent_parent_mismatched" in {
        warning.metadata["code"] for warning in bundle.parse_warnings
    }


def test_claude_malformed_and_mismatched_metadata_warns(tmp_path) -> None:
    project_dir = tmp_path / "project"
    subagents_dir = project_dir / "session-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (project_dir / "session-1.jsonl").write_text(
        json.dumps(assistant_agent_record("root-parent", "agent-tool", sidechain=False))
    )
    malformed_path = subagents_dir / "agent-malformed.jsonl"
    malformed_record = assistant_text_record(
        "malformed", sidechain=True, agent_id="agent-malformed"
    )
    malformed_record["sourceToolAssistantUUID"] = "root-parent"
    malformed_path.write_text(json.dumps(malformed_record))
    malformed_path.with_suffix(".meta.json").write_text("not json")
    mismatch_path = subagents_dir / "agent-mismatch.jsonl"
    mismatch_record = assistant_text_record("mismatch", sidechain=True, agent_id="agent-mismatch")
    mismatch_record["sourceToolAssistantUUID"] = "root-parent"
    mismatch_path.write_text(json.dumps(mismatch_record))
    mismatch_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentId": "different-agent", "agentType": "Explore"})
    )
    adapter = ClaudeCodeAdapter()
    sources = {Path(source.source_path): source for source in adapter.discover(tmp_path)}

    malformed_bundle = adapter.parse_live_source(sources[malformed_path])
    mismatch_bundle = adapter.parse_live_source(sources[mismatch_path])

    assert "subagent_metadata_malformed" in {
        warning.metadata["code"] for warning in malformed_bundle.parse_warnings
    }
    assert "subagent_metadata_mismatched" in {
        warning.metadata["code"] for warning in mismatch_bundle.parse_warnings
    }


def test_claude_missing_and_unsafe_tool_result_sidecars_warn(tmp_path) -> None:
    root_path = tmp_path / "session-1.jsonl"
    session_dir = tmp_path / "session-1"
    (session_dir / "tool-results").mkdir(parents=True)
    records = []
    for index, persisted_path in enumerate(
        ("tool-results/missing.txt", str(tmp_path / "outside.txt"))
    ):
        record = {
            "type": "user",
            "sessionId": "session-1",
            "uuid": f"result-{index}",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": f"tool-{index}"}],
            },
            "toolUseResult": {"persistedOutputPath": persisted_path},
        }
        records.append(record)
    root_path.write_text("\n".join(json.dumps(record) for record in records))

    bundle = ClaudeCodeAdapter().parse_live_source(ClaudeCodeAdapter().source_for_path(root_path))

    assert {warning.metadata["code"] for warning in bundle.parse_warnings} >= {
        "missing_tool_result_sidecar",
        "unsafe_tool_result_sidecar_path",
    }
    assert str(tmp_path / "outside.txt") not in bundle.model_dump_json()


def assistant_agent_record(uuid: str, tool_use_id: str, *, sidechain: bool) -> dict:
    return {
        "type": "assistant",
        "sessionId": "session-1",
        "uuid": uuid,
        "isSidechain": sidechain,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "Agent",
                    "input": {},
                }
            ],
        },
    }


def assistant_text_record(
    uuid: str,
    *,
    sidechain: bool,
    agent_id: str,
) -> dict:
    return {
        "type": "assistant",
        "sessionId": "session-1",
        "uuid": uuid,
        "agentId": agent_id,
        "isSidechain": sidechain,
        "message": {"role": "assistant", "content": []},
    }

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    ToolResult,
)


def analysis_fixture_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 11)
    ]
    messages = [
        message(
            "message-1",
            NormalizedRole.USER,
            "Please fix the failing pytest in tests/test_cli.py.",
            "event-1",
        ),
        message("message-2", NormalizedRole.ASSISTANT, "I will run the tests.", "event-2"),
        message(
            "message-3",
            NormalizedRole.USER,
            "Please fix the pytest failure in tests/test_cli.py.",
            "event-3",
        ),
        message(
            "message-4",
            NormalizedRole.USER,
            "No, that is not what I meant. It is still broken.",
            "event-8",
        ),
        message(
            "message-5",
            NormalizedRole.USER,
            "Before you change more code, keep it to small commits.",
            "event-9",
        ),
    ]
    command_runs = [
        CommandRun(
            command_run_id="command-1",
            session_id=session.session_id,
            source_event_id="event-5",
            command="pytest -q",
            exit_code=1,
            stdout_hash="hash-failure",
        ),
        CommandRun(
            command_run_id="command-2",
            session_id=session.session_id,
            source_event_id="event-10",
            command="pytest -q",
            exit_code=1,
            stdout_hash="hash-failure",
        ),
    ]
    tool_results = [
        ToolResult(
            tool_result_id="tool-result-1",
            session_id=session.session_id,
            source_event_id="event-6",
            is_error=True,
            output_hash="tool-error-hash",
        )
    ]
    file_activities = [
        FileActivity(
            file_activity_id="file-1",
            session_id=session.session_id,
            source_event_id="event-4",
            path="/tmp/example.py",
            operation="update",
        ),
        FileActivity(
            file_activity_id="file-2",
            session_id=session.session_id,
            source_event_id="event-7",
            path="/tmp/example.py",
            operation="update",
        ),
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
        tool_results=tool_results,
        file_activities=file_activities,
    )


def resolved_after_correction_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 5)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please fix the failing test.", "event-1"),
        message(
            "message-2",
            NormalizedRole.USER,
            "No, that is not what I meant.",
            "event-2",
        ),
        message(
            "message-3",
            NormalizedRole.ASSISTANT,
            "Fixed and verified.",
            "event-4",
            metadata={"phase": "final_answer"},
        ),
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def resolved_failed_command_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 5)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please run the test.", "event-1"),
        message(
            "message-2",
            NormalizedRole.ASSISTANT,
            "The failure is fixed now.",
            "event-4",
            metadata={"phase": "final_answer"},
        ),
    ]
    command_runs = [
        CommandRun(
            command_run_id="command-1",
            session_id=session.session_id,
            source_event_id="event-3",
            command="pytest -q",
            exit_code=1,
        )
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
    )


def clean_finished_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 4)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please summarize the repository.", "event-1"),
        message(
            "message-2",
            NormalizedRole.ASSISTANT,
            "Summary complete.",
            "event-3",
            metadata={"phase": "final_answer"},
        ),
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def broad_low_friction_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 21)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Implement the planned changes.", "event-1"),
        message(
            "message-2",
            NormalizedRole.ASSISTANT,
            "Implemented and tested.",
            "event-20",
            metadata={"phase": "final_answer"},
        ),
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 1}",
            command=f"command {index}",
            exit_code=0,
        )
        for index in range(1, 13)
    ]
    tool_results = [
        ToolResult(
            tool_result_id=f"tool-result-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 9}",
            is_error=False,
        )
        for index in range(1, 21)
    ]
    file_activities = [
        FileActivity(
            file_activity_id=f"file-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 1}",
            path=f"src/file_{index}.py",
            operation="edit",
        )
        for index in range(1, 9)
    ] + [
        FileActivity(
            file_activity_id=f"file-repeat-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 8}",
            path="src/file_1.py",
            operation="edit",
        )
        for index in range(1, 6)
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
        tool_results=tool_results,
        file_activities=file_activities,
    )


def prompt_ambiguous_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 5)
    ]
    messages = [
        message(
            "message-1",
            NormalizedRole.USER,
            "I am not sure which one to update; only touch docs.",
            "event-1",
        ),
        message(
            "message-2",
            NormalizedRole.USER,
            "This is unclear, do not change code; wrong target.",
            "event-2",
        ),
        message(
            "message-3",
            NormalizedRole.USER,
            "The request is ambiguous, I meant the phase plan only.",
            "event-3",
        ),
        message(
            "message-4",
            NormalizedRole.USER,
            "Can you clarify before you continue? That is not what I meant.",
            "event-4",
        ),
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def prompt_ambiguous_with_tool_failure_bundle() -> ParsedSessionBundle:
    bundle = prompt_ambiguous_bundle()
    assert bundle.session is not None
    return bundle.model_copy(
        update={
            "tool_results": [
                ToolResult(
                    tool_result_id="tool-result-1",
                    session_id=bundle.session.session_id,
                    source_event_id="event-4",
                    is_error=True,
                )
            ]
        }
    )


def complex_high_friction_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 31)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Implement the broad refactor.", "event-1")
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 1}",
            command=f"pytest shard {index}",
            exit_code=1,
            stderr_hash="shared-complex-failure",
        )
        for index in range(1, 13)
    ]
    tool_results = [
        ToolResult(
            tool_result_id=f"tool-result-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 13}",
            is_error=False,
        )
        for index in range(1, 21)
    ]
    file_activities = [
        FileActivity(
            file_activity_id=f"file-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 1}",
            path=f"src/file_{index}.py",
            operation="edit",
        )
        for index in range(1, 9)
    ] + [
        FileActivity(
            file_activity_id=f"file-repeat-{path_index}-{edit_index}",
            session_id=session.session_id,
            source_event_id=f"event-{path_index + edit_index + 8}",
            path=f"src/file_{path_index}.py",
            operation="edit",
        )
        for path_index in range(1, 4)
        for edit_index in range(1, 6)
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
        tool_results=tool_results,
        file_activities=file_activities,
    )


def tooling_loop_without_user_stuck_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 15)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please run the implementation.", "event-1")
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 1}",
            command=f"pytest shard {index}",
            exit_code=1,
            stderr_hash="shared-loop-failure",
        )
        for index in range(1, 6)
    ]
    file_activities = [
        FileActivity(
            file_activity_id=f"file-{path_index}-{edit_index}",
            session_id=session.session_id,
            source_event_id=f"event-{path_index + edit_index + 6}",
            path=f"src/file_{path_index}.py",
            operation="edit",
        )
        for path_index in range(1, 4)
        for edit_index in range(1, 3)
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
        file_activities=file_activities,
    )


def abandoned_or_stopped_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 7)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please update the plan.", "event-1"),
        message("message-2", NormalizedRole.ASSISTANT, "I will update it.", "event-2"),
        message("message-3", NormalizedRole.USER, "Never mind, we can stop.", "event-6"),
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def bursty_timestamp_window_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    start = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
            timestamp=start + timedelta(seconds=index),
        )
        for index in range(1, 31)
    ]
    messages = [
        message(
            "message-1",
            NormalizedRole.USER,
            "No, that is not what I meant.",
            "event-5",
        )
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def timestamp_window_parse_warning_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    start = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
            timestamp=start + timedelta(seconds=index),
        )
        for index in range(1, 31)
    ]
    parse_warnings = [
        ParseWarning(
            warning_id="warning-1",
            source_id="source-1",
            record_index=5,
            severity="warning",
            message="Unsupported late timestamp-window record shape.",
        )
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        parse_warnings=parse_warnings,
    )


def malformed_final_record_parse_warning_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 30)
    ]
    parse_warnings = [
        ParseWarning(
            warning_id="warning-1",
            source_id="source-1",
            record_index=30,
            severity="error",
            message="Malformed final JSONL record.",
        )
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        parse_warnings=parse_warnings,
    )


def repeated_command_failure_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 4)
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            command="pytest -q",
            exit_code=1,
        )
        for index in range(1, 4)
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, command_runs=command_runs)


def repeated_tool_result_failure_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 4)
    ]
    tool_results = [
        ToolResult(
            tool_result_id=f"tool-result-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            is_error=True,
            output_hash="same-tool-error",
        )
        for index in range(1, 4)
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, tool_results=tool_results)


def shared_stderr_distinct_command_failure_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 4)
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            command=f"pytest tests/test_{index}.py",
            exit_code=1,
            stderr_hash="shared-stderr",
        )
        for index in range(1, 4)
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, command_runs=command_runs)


def mixed_small_command_and_tool_failure_groups_bundle() -> ParsedSessionBundle:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 5)
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            command="pytest -q",
            exit_code=1,
        )
        for index in range(1, 3)
    ]
    tool_results = [
        ToolResult(
            tool_result_id=f"tool-result-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index + 2}",
            is_error=True,
            output_hash="same-tool-error",
        )
        for index in range(1, 3)
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        command_runs=command_runs,
        tool_results=tool_results,
    )


def message(
    message_id: str,
    role: NormalizedRole,
    text: str,
    source_event_id: str,
    metadata: dict[str, object] | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        session_id="session-1",
        role=role,
        source_event_id=source_event_id,
        text=text,
        text_length=len(text),
        metadata=metadata or {},
    )

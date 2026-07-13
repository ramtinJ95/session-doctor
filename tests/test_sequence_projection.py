from session_doctor.sequence_projection import (
    MAX_SEQUENCE_BINS,
    SequenceActivity,
    sequence_bins,
)


def test_sequence_bins_are_bounded_contiguous_and_reconcile_sparse_activity() -> None:
    bins = sequence_bins(
        [
            SequenceActivity("user_message", 1),
            SequenceActivity("command_failure", 500),
            SequenceActivity("file_activity", 1000),
            SequenceActivity("tool_call", None),
        ],
        1,
        1000,
    )

    assert len(bins) == MAX_SEQUENCE_BINS
    assert bins[0].first_record_index == 1
    assert bins[-1].last_record_index == 1000
    assert all(
        left.last_record_index + 1 == right.first_record_index
        for left, right in zip(bins, bins[1:], strict=False)
    )
    assert sum(sum(row.counts.model_dump().values()) for row in bins) == 3
    assert sum(row.counts.user_message for row in bins) == 1
    assert sum(row.counts.command_failure for row in bins) == 1
    assert sum(row.counts.file_activity for row in bins) == 1
    assert sum(row.counts.tool_call for row in bins) == 0


def test_sequence_activity_categories_and_warning_resolution_are_exact(tmp_path) -> None:
    from session_doctor.adapters import ParsedSessionBundle
    from session_doctor.report_payload import build_session_report
    from session_doctor.schemas import (
        AgentName,
        CommandRun,
        Message,
        NormalizedRole,
        ParseWarning,
        RawEvent,
        Session,
        SessionSource,
        ToolCall,
        ToolResult,
    )
    from session_doctor.store import DuckDBStore

    source = SessionSource(
        source_id="source-sequence",
        agent_name=AgentName.CODEX,
        source_path="/private/source.jsonl",
    )
    session = Session(
        session_id="sequence-session",
        source_id=source.source_id,
        agent_name=source.agent_name,
    )
    store = DuckDBStore(tmp_path / "sequence.duckdb")
    store.insert_untracked_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=session,
            raw_events=[
                RawEvent(
                    event_id="event-zero",
                    source_id=source.source_id,
                    agent_name=source.agent_name,
                    record_index=0,
                ),
                RawEvent(
                    event_id="event-two-a",
                    source_id=source.source_id,
                    agent_name=source.agent_name,
                    record_index=2,
                ),
                RawEvent(
                    event_id="event-two-b",
                    source_id=source.source_id,
                    agent_name=source.agent_name,
                    record_index=2,
                ),
            ],
            messages=[
                Message(
                    message_id="user",
                    session_id=session.session_id,
                    role=NormalizedRole.USER,
                    source_event_id="event-zero",
                    text="PRIVATE USER TEXT",
                ),
                Message(
                    message_id="assistant",
                    session_id=session.session_id,
                    role=NormalizedRole.ASSISTANT,
                    source_event_id="event-zero",
                ),
                Message(
                    message_id="system",
                    session_id=session.session_id,
                    role=NormalizedRole.SYSTEM,
                    source_event_id="event-zero",
                ),
            ],
            tool_calls=[
                ToolCall(
                    tool_call_id="missing-call",
                    session_id=session.session_id,
                    source_event_id="missing-event",
                    name="shell",
                )
            ],
            tool_results=[
                ToolResult(
                    tool_result_id="result-ok",
                    session_id=session.session_id,
                    source_event_id="event-zero",
                    is_error=False,
                ),
                ToolResult(
                    tool_result_id="result-failed",
                    session_id=session.session_id,
                    source_event_id="event-zero",
                    is_error=True,
                ),
            ],
            command_runs=[
                CommandRun(
                    command_run_id="command-ok",
                    session_id=session.session_id,
                    source_event_id="event-zero",
                    command="true",
                    exit_code=0,
                ),
                CommandRun(
                    command_run_id="command-unknown",
                    session_id=session.session_id,
                    source_event_id="event-zero",
                    command="opaque",
                ),
                CommandRun(
                    command_run_id="command-cancelled",
                    session_id=session.session_id,
                    source_event_id="event-zero",
                    command="cancelled",
                    exit_code=0,
                    metadata={"cancelled": True},
                ),
            ],
            parse_warnings=[
                ParseWarning(
                    warning_id="warning-exact",
                    source_id=source.source_id,
                    record_index=0,
                    message="PRIVATE WARNING",
                ),
                ParseWarning(
                    warning_id="warning-ambiguous",
                    source_id=source.source_id,
                    record_index=2,
                    message="PRIVATE AMBIGUOUS WARNING",
                ),
            ],
        ),
    )
    snapshot = store.load_diagnostic_snapshot(session.session_id)
    assert snapshot is not None

    sequence = build_session_report(snapshot).sequence
    serialized = sequence.model_dump_json()

    assert sequence.resolved_activity_counts.model_dump() == {
        "user_message": 1,
        "assistant_message": 1,
        "tool_call": 0,
        "tool_result": 1,
        "tool_failure": 1,
        "command_success": 1,
        "command_failure": 1,
        "command_unknown": 1,
        "file_activity": 0,
        "parse_warning": 1,
    }
    assert sequence.unresolved_activity_counts.tool_call == 1
    assert sequence.unresolved_activity_counts.parse_warning == 1
    assert sequence.total_resolved_activities == 8
    assert sequence.total_unresolved_activities == 2
    assert "PRIVATE" not in serialized
    assert "metadata" not in serialized

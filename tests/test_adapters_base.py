from __future__ import annotations

from session_doctor.adapters import ClaudeCodeAdapter, CodexAdapter, PiAdapter
from session_doctor.adapters.claude import classify_claude_path
from session_doctor.schemas import AgentName, SourceKind


def test_codex_discovery_finds_jsonl_sessions(tmp_path) -> None:
    session_path = tmp_path / "2026" / "05" / "04" / "rollout.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text('{"type":"session_meta"}\n')
    (session_path.parent / "notes.txt").write_text("ignore me")

    sources = CodexAdapter().discover(tmp_path)

    assert len(sources) == 1
    assert sources[0].agent_name == AgentName.CODEX
    assert sources[0].source_kind == SourceKind.ROOT_SESSION


def test_pi_discovery_finds_jsonl_sessions(tmp_path) -> None:
    session_path = tmp_path / "--project--" / "2026-05-04T.jsonl"
    session_path.parent.mkdir()
    session_path.write_text('{"type":"session"}\n')

    sources = PiAdapter().discover(tmp_path)

    assert len(sources) == 1
    assert sources[0].agent_name == AgentName.PI


def test_claude_discovery_classifies_known_file_kinds(tmp_path) -> None:
    root_session = tmp_path / "project" / "session.jsonl"
    subagent_session = tmp_path / "project" / "session" / "subagents" / "agent-a.jsonl"
    subagent_meta = tmp_path / "project" / "session" / "subagents" / "agent-a.meta.json"
    tool_result = tmp_path / "project" / "session" / "tool-results" / "result.txt"
    memory_file = tmp_path / "project" / "memory.md"
    auxiliary_file = tmp_path / "project" / "custom-title"

    for path in (
        root_session,
        subagent_session,
        subagent_meta,
        tool_result,
        memory_file,
        auxiliary_file,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")

    sources = ClaudeCodeAdapter().discover(tmp_path)
    by_path = {source.source_path: source.source_kind for source in sources}

    assert by_path[str(root_session)] == SourceKind.ROOT_SESSION
    assert by_path[str(subagent_session)] == SourceKind.SUBSESSION
    assert by_path[str(subagent_meta)] == SourceKind.SUBAGENT_METADATA
    assert by_path[str(tool_result)] == SourceKind.TOOL_RESULT
    assert by_path[str(memory_file)] == SourceKind.MEMORY
    assert by_path[str(auxiliary_file)] == SourceKind.AUXILIARY


def test_claude_parse_source_is_implemented(tmp_path) -> None:
    source_path = tmp_path / "session.jsonl"
    source_path.write_text(
        '{"type":"user","sessionId":"session-1","uuid":"message-1",'
        '"timestamp":"2026-01-01T00:00:00Z","message":'
        '{"role":"user","content":"Hello"}}\n'
    )
    adapter = ClaudeCodeAdapter()

    bundle = adapter.parse_source(adapter.source_for_path(source_path))

    assert bundle.session is not None
    assert bundle.session.native_session_id == "session-1"
    assert len(bundle.raw_events) == 1
    assert len(bundle.messages) == 1


def test_classify_claude_path_prefers_tool_results(tmp_path) -> None:
    path = tmp_path / "project" / "session" / "tool-results" / "result.txt"

    assert classify_claude_path(path, tmp_path) == SourceKind.TOOL_RESULT


def test_claude_discovery_classifies_layout_names_relative_to_root(tmp_path) -> None:
    root_named_tool_results = tmp_path / "tool-results" / "session.jsonl"
    project_named_subagents = tmp_path / "subagents" / "session.jsonl"
    real_subagent = tmp_path / "project" / "session" / "subagents" / "agent-a.jsonl"

    for path in (root_named_tool_results, project_named_subagents, real_subagent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")

    sources = ClaudeCodeAdapter().discover(tmp_path)
    by_path = {source.source_path: source.source_kind for source in sources}

    assert by_path[str(root_named_tool_results)] == SourceKind.ROOT_SESSION
    assert by_path[str(project_named_subagents)] == SourceKind.ROOT_SESSION
    assert by_path[str(real_subagent)] == SourceKind.SUBSESSION

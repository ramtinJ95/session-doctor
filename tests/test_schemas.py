from __future__ import annotations

import pytest
from pydantic import ValidationError

from session_doctor.ids import stable_id
from session_doctor.privacy import (
    hash_text,
    looks_sensitive_key,
    redact_command_for_display,
    redact_home,
    text_length,
)
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    GraphEdge,
    Message,
    MessageFeature,
    NormalizedRole,
    Session,
    SessionClassification,
    SessionFeature,
    SessionSource,
    SourceKind,
)


def test_session_source_serializes_to_plain_dict() -> None:
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/tmp/session.jsonl",
    )

    assert source.model_dump(mode="json") == {
        "source_id": "source-1",
        "agent_name": "codex",
        "source_path": "/tmp/session.jsonl",
        "source_kind": "root_session",
        "discovered_at": None,
        "native_session_id": None,
        "parent_source_id": None,
        "metadata": {},
    }


def test_session_model_accepts_minimal_fields() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.PI,
    )

    assert session.agent_name == AgentName.PI
    assert session.is_sidechain is False


def test_message_model_stores_user_text_for_future_classification() -> None:
    text = "Please fix the same error again."
    message = Message(
        message_id="message-1",
        session_id="session-1",
        role=NormalizedRole.USER,
        text=text,
        text_hash=hash_text(text),
        text_length=text_length(text),
        content_block_types=["input_text"],
    )

    assert message.text == text
    assert message.text_length == len(text)
    assert len(message.text_hash or "") == 64


def test_enum_validation_rejects_unknown_agent_string() -> None:
    with pytest.raises(ValidationError):
        SessionSource.model_validate(
            {
                "source_id": "source-1",
                "agent_name": "not-an-agent",
                "source_path": "/tmp/session.jsonl",
            }
        )


def test_graph_edge_confidence_bounds() -> None:
    edge = GraphEdge(
        edge_id="edge-1",
        session_id="session-1",
        source_node_id="node-1",
        target_node_id="node-2",
        edge_type="responds_to",
        confidence=0.75,
    )
    assert edge.confidence == 0.75

    with pytest.raises(ValidationError):
        GraphEdge(
            edge_id="edge-2",
            session_id="session-1",
            source_node_id="node-1",
            target_node_id="node-2",
            edge_type="responds_to",
            confidence=2.0,
        )


def test_analysis_models_validate_confidence_bounds() -> None:
    run = AnalysisRun(
        analysis_run_id="analysis-1",
        session_id="session-1",
        analyzer_version="phase3",
    )
    assert run.artifact_path is None

    message_feature = MessageFeature(
        message_feature_id="message-feature-1",
        analysis_run_id=run.analysis_run_id,
        session_id=run.session_id,
        message_id="message-1",
        feature_name="correction_marker",
        feature_value="true",
        score=0.75,
    )
    assert message_feature.score == 0.75

    session_feature = SessionFeature(
        session_feature_id="session-feature-1",
        analysis_run_id=run.analysis_run_id,
        session_id=run.session_id,
        feature_name="correction_count",
        feature_value="1",
    )
    assert session_feature.score == 1.0

    classification = SessionClassification(
        session_classification_id="classification-1",
        analysis_run_id=run.analysis_run_id,
        session_id=run.session_id,
        label="user_stuck",
        score=0.8,
        confidence=0.7,
        evidence_summary="Repeated request and correction evidence.",
    )
    assert classification.evidence_event_ids == []

    with pytest.raises(ValidationError):
        SessionClassification(
            session_classification_id="classification-2",
            analysis_run_id=run.analysis_run_id,
            session_id=run.session_id,
            label="user_stuck",
            score=1.2,
            confidence=0.7,
            evidence_summary="Invalid score.",
        )


def test_stable_id_is_deterministic() -> None:
    assert stable_id("codex", "path", 1) == stable_id("codex", "path", 1)
    assert stable_id("codex", "path", 1) != stable_id("codex", "path", 2)


def test_privacy_helpers() -> None:
    assert len(hash_text("hello")) == 64
    assert text_length(None) == 0
    assert redact_home("relative/path") == "relative/path"
    assert looks_sensitive_key("OPENAI_API_KEY")
    assert "secret=<redacted>" in redact_command_for_display("run secret=value")


def test_source_kind_has_claude_discovery_categories() -> None:
    assert SourceKind.SUBSESSION.value == "subsession"
    assert SourceKind.TOOL_RESULT.value == "tool_result"

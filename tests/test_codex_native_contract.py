from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures/codex/current-response-items.jsonl"
AMBIGUITIES = Path(__file__).parent / "fixtures/codex/current-response-item-ambiguities.jsonl"
SCAN = Path(__file__).parents[1] / "docs/codex-native-format-scan.json"


def load_records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_current_codex_fixture_is_wholly_synthetic() -> None:
    fixture = FIXTURE.read_text()

    assert "synthetic" in fixture
    assert "/Users/" not in fixture
    assert "ramtin" not in fixture.lower()
    assert "session-doctor" not in fixture
    assert "PRIVATE_" not in fixture


def test_current_codex_fixture_covers_execution_contract() -> None:
    records = load_records()
    response_payloads = [
        record["payload"] for record in records if record["type"] == "response_item"
    ]
    calls = [
        payload
        for payload in response_payloads
        if payload["type"] in {"function_call", "custom_tool_call"}
    ]
    outputs = [
        payload
        for payload in response_payloads
        if payload["type"] in {"function_call_output", "custom_tool_call_output"}
    ]
    call_counts = Counter(payload["call_id"] for payload in calls)
    output_counts = Counter(payload["call_id"] for payload in outputs)
    calls_by_id = {payload["call_id"]: payload for payload in calls}
    outputs_by_id = {payload["call_id"]: payload for payload in outputs}

    assert call_counts == Counter({call_id: 1 for call_id in calls_by_id})
    assert output_counts == Counter({call_id: 1 for call_id in outputs_by_id})
    assert set(calls_by_id) == set(outputs_by_id)
    assert {payload["name"] for payload in calls} == {
        "exec",
        "exec_command",
        "write_stdin",
    }
    assert isinstance(calls_by_id["synthetic-exec"]["input"], str)
    assert json.loads(calls_by_id["synthetic-exec-command"]["arguments"])["cmd"]
    assert "Process exited with code 1" in outputs_by_id["synthetic-exec-command"]["output"]
    assert "Process running with session ID 42" in outputs_by_id["synthetic-long-exec"]["output"]


def test_current_codex_fixture_covers_newer_record_contract() -> None:
    records = load_records()
    pairs = {(record["type"], record["payload"].get("type")) for record in records}

    assert ("response_item", "agent_message") in pairs
    assert ("event_msg", "sub_agent_activity") in pairs
    assert ("inter_agent_communication_metadata", None) in pairs
    assert ("response_item", "tool_search_call") in pairs
    assert ("response_item", "tool_search_output") in pairs


def test_current_codex_fixture_has_exact_tool_search_pair() -> None:
    records = load_records()
    tool_search = [
        record["payload"]
        for record in records
        if record["type"] == "response_item"
        and record["payload"].get("type") in {"tool_search_call", "tool_search_output"}
    ]

    assert [payload["call_id"] for payload in tool_search] == [
        "synthetic-tool-search",
        "synthetic-tool-search",
    ]


def test_current_codex_ambiguity_fixture_covers_each_cardinality() -> None:
    records = [json.loads(line) for line in AMBIGUITIES.read_text().splitlines()]
    payloads = [record["payload"] for record in records if record["type"] == "response_item"]
    calls = Counter(
        payload["call_id"] for payload in payloads if payload["type"] == "function_call"
    )
    outputs = Counter(
        payload["call_id"] for payload in payloads if payload["type"] == "function_call_output"
    )

    assert calls == Counter(
        {
            "synthetic-duplicate-call": 2,
            "synthetic-multiple-outputs": 1,
            "synthetic-missing-output": 1,
        }
    )
    assert outputs == Counter(
        {
            "synthetic-duplicate-call": 1,
            "synthetic-multiple-outputs": 2,
            "synthetic-orphan-output": 1,
        }
    )


def test_retained_native_scan_is_structural_only() -> None:
    scan_text = SCAN.read_text()
    scan = json.loads(scan_text)

    assert scan["source_files"] == 70
    assert scan["response_item_execution"]["exec_command"]["calls"] == 1775
    assert scan["response_item_execution"]["exec"]["calls"] == 1234
    assert scan["response_item_execution"]["write_stdin"]["calls"] == 226
    assert "source_path" in scan["excluded_fields"]
    assert "output_text" in scan["excluded_fields"]
    assert "/Users/" not in scan_text
    assert "ramtin" not in scan_text.lower()

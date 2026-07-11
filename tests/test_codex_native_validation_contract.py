from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
PLAN = ROOT / "docs/codex-native-format-update-plan.md"
VALIDATION = ROOT / "docs/codex-native-format-validation.md"
SCAN = ROOT / "docs/codex-native-format-scan.json"


def test_codex_native_update_is_complete_and_privacy_safe() -> None:
    plan = PLAN.read_text()
    validation = VALIDATION.read_text()
    scan = json.loads(SCAN.read_text())

    assert "Status: complete." in plan
    assert "command runs | 0 | 3,009" in validation
    assert "unsupported-format warnings | 202 | 0" in validation
    assert "current machine-wide analysis coverage: 285/285" in validation
    assert "report/graph row-count mutation | 0" in validation
    assert "pytest: 284 passed" in validation
    assert scan["response_item_execution"]["exec_command"]["calls"] == 1775
    assert scan["response_item_execution"]["exec"]["calls"] == 1234
    forbidden = (
        "/Users/",
        'source_path":',
        "native_session_id",
        "arguments_hash",
        "output_hash",
        "PRIVATE_",
    )
    assert all(marker not in validation for marker in forbidden)

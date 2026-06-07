from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import JsonRecord, dict_value, string_value
from .pi_commands import bash_execution_parent_record_ids
from .pi_tool_calls import arguments_from_tool_call_block


@dataclass
class PiCommandCorrelation:
    tool_call_arguments_by_id: dict[str, dict[str, Any]]
    tool_call_id_by_tool_result_id: dict[str, str]
    bash_execution_parent_ids: set[str]

    @classmethod
    def from_records(cls, records: list[JsonRecord]) -> PiCommandCorrelation:
        return cls(
            tool_call_arguments_by_id={},
            tool_call_id_by_tool_result_id={},
            bash_execution_parent_ids=bash_execution_parent_record_ids(records),
        )

    def remember_tool_call_arguments(
        self,
        native_tool_call_id: str | None,
        block: dict[str, Any],
    ) -> None:
        if native_tool_call_id is None:
            return
        self.tool_call_arguments_by_id[native_tool_call_id] = arguments_from_tool_call_block(block)

    def remember_tool_result_link(self, record: dict[str, Any]) -> None:
        message_payload = dict_value(record.get("message"))
        call_id = string_value(message_payload.get("toolCallId"))
        native_tool_result_id = string_value(record.get("id"))
        if call_id and native_tool_result_id:
            self.tool_call_id_by_tool_result_id[native_tool_result_id] = call_id

    def has_bash_execution_result(self, record: dict[str, Any]) -> bool:
        native_tool_result_id = string_value(record.get("id"))
        return native_tool_result_id in self.bash_execution_parent_ids

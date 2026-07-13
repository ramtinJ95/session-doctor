from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.schemas import ModelUsage, RawEvent, UsageSemantics

from .common import dict_value, int_value, string_value


def model_usage_from_message(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> ModelUsage | None:
    message_payload = dict_value(record.get("message"))
    usage = dict_value(message_payload.get("usage"))
    if not usage:
        return None
    cost = cost_from_usage(usage)
    return ModelUsage(
        model_usage_id=stable_id("model_usage", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        timestamp=event.timestamp,
        provider=string_value(message_payload.get("provider")),
        model=string_value(message_payload.get("model")),
        input_tokens=int_value(usage.get("input")),
        output_tokens=int_value(usage.get("output")),
        cache_read_tokens=int_value(usage.get("cacheRead")),
        cache_write_tokens=int_value(usage.get("cacheWrite")),
        total_tokens=int_value(usage.get("totalTokens")),
        cost=cost,
        aggregation_semantics=UsageSemantics.INCREMENTAL,
        metadata={
            "cost": usage.get("cost"),
            "stop_reason": string_value(message_payload.get("stopReason")),
        },
    )


def cost_from_usage(usage: dict[str, Any]) -> Decimal | None:
    cost = usage.get("cost")
    if isinstance(cost, dict):
        cost = cost.get("total")
    return decimal_value(cost)


def decimal_value(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None

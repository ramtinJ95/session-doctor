from __future__ import annotations

from typing import Any

from .common import bool_value, dict_value, int_value, string_value, text_from_content


def tool_result_output(message_payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    content_text = text_from_content(message_payload.get("content"), text_block_types={"text"})
    if content_text:
        parts.append(content_text)
    details_text = text_from_details(dict_value(message_payload.get("details")))
    if details_text:
        parts.append(details_text)
    return "\n".join(parts) if parts else None


DETAIL_TEXT_KEYS = {
    "aggregated_output",
    "content",
    "diff",
    "error",
    "errorMessage",
    "formatted_output",
    "message",
    "output",
    "patch",
    "result",
    "stderr",
    "stdout",
    "text",
}


def text_from_details(value: object) -> str | None:
    texts: list[str] = []
    collect_detail_text(value, texts, depth=0)
    return "\n".join(texts) if texts else None


def collect_detail_text(value: object, texts: list[str], *, depth: int) -> None:
    if depth > 2:
        return
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key in DETAIL_TEXT_KEYS:
                nested_text = string_value(nested_value)
                if nested_text:
                    texts.append(nested_text)
            if isinstance(nested_value, dict | list):
                collect_detail_text(nested_value, texts, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict | list):
                collect_detail_text(item, texts, depth=depth + 1)


def tool_result_is_error(message_payload: dict[str, Any]) -> bool | None:
    message_is_error = bool_value(message_payload.get("isError"))
    if message_is_error is True:
        return True
    if details_have_failure_signal(dict_value(message_payload.get("details"))):
        return True
    return message_is_error


DETAIL_FAILURE_STATUS_VALUES = {
    "cancelled",
    "canceled",
    "error",
    "errored",
    "failed",
    "failure",
    "timed_out",
    "timeout",
}

DETAIL_FAILURE_BOOL_KEYS = {
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
    "isError",
    "is_error",
    "timedOut",
    "timed_out",
}

DETAIL_FAILURE_TEXT_KEYS = {
    "error",
    "errorCode",
    "error_code",
    "errorMessage",
    "error_message",
}

DETAIL_STATUS_KEYS = {"outcome", "state", "status"}
DETAIL_SUCCESS_BOOL_KEYS = {"ok", "success"}


def details_have_failure_signal(value: object, *, depth: int = 0) -> bool:
    if depth > 2 or not isinstance(value, dict):
        return False
    payload = dict_value(value)
    for key, nested_value in payload.items():
        if key in ("exitCode", "exit_code"):
            exit_code = int_value(nested_value)
            if exit_code is not None and exit_code != 0:
                return True
        if key in DETAIL_FAILURE_BOOL_KEYS and bool_value(nested_value) is True:
            return True
        if key in DETAIL_SUCCESS_BOOL_KEYS and bool_value(nested_value) is False:
            return True
        if key in DETAIL_FAILURE_TEXT_KEYS and string_value(nested_value):
            return True
        if key in DETAIL_STATUS_KEYS:
            status = string_value(nested_value)
            if status and status.lower().replace("-", "_") in DETAIL_FAILURE_STATUS_VALUES:
                return True
        if isinstance(nested_value, dict) and details_have_failure_signal(
            nested_value,
            depth=depth + 1,
        ):
            return True
        if isinstance(nested_value, list):
            for item in nested_value:
                if isinstance(item, dict) and details_have_failure_signal(
                    item,
                    depth=depth + 1,
                ):
                    return True
    return False


def exit_code_from_tool_result(message_payload: dict[str, Any]) -> int | None:
    details = dict_value(message_payload.get("details"))
    exit_code = exit_code_from_details(details)
    if exit_code is not None:
        return exit_code
    is_error = bool_value(message_payload.get("isError"))
    if is_error is True:
        return 1
    if is_error is False:
        return 0
    return None


def exit_code_from_details(value: object, *, depth: int = 0) -> int | None:
    if depth > 2:
        return None
    if not isinstance(value, dict):
        return None
    payload = dict_value(value)
    for key in ("exitCode", "exit_code"):
        exit_code = int_value(payload.get(key))
        if exit_code is not None:
            return exit_code
    for nested_value in payload.values():
        if isinstance(nested_value, dict):
            exit_code = exit_code_from_details(nested_value, depth=depth + 1)
            if exit_code is not None:
                return exit_code
    return None

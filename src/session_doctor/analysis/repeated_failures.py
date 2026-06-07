from __future__ import annotations

from collections import defaultdict

from session_doctor.adapters import ParsedSessionBundle


def repeated_failure_groups(bundle: ParsedSessionBundle) -> list[dict[str, object]]:
    group_values: defaultdict[tuple[str, str], list[tuple[str, str | None]]] = defaultdict(list)
    for command in bundle.command_runs:
        if command.exit_code is None or command.exit_code == 0:
            continue
        if command.stderr_hash:
            group_values[("command_stderr_hash", f"stderr_hash:{command.stderr_hash}")].append(
                (command.command_run_id, command.source_event_id),
            )
        if command.stdout_hash:
            group_values[("command_stdout_hash", f"stdout_hash:{command.stdout_hash}")].append(
                (command.command_run_id, command.source_event_id),
            )
        group_values[("failed_command_text", f"failed_command:{command.command}")].append(
            (command.command_run_id, command.source_event_id),
        )

    for result in bundle.tool_results:
        if result.is_error is not True or not result.output_hash:
            continue
        group_values[("tool_output_hash", f"tool_output_hash:{result.output_hash}")].append(
            (result.tool_result_id, result.source_event_id)
        )

    return [
        {
            "key": key,
            "group_type": group_type,
            "record_ids": sorted(record_id for record_id, _ in records),
            "source_event_ids": sorted(
                {source_event_id for _, source_event_id in records if source_event_id}
            ),
            "repeat_count": len(records) - 1,
        }
        for (group_type, key), records in sorted(group_values.items())
        if len(records) > 1
    ]


def repeated_command_loop_failure_groups(
    groups: list[dict[str, object]],
) -> list[dict[str, object]]:
    command_loop_group_types = {
        "failed_command_text",
        "command_stdout_hash",
        "command_stderr_hash",
    }
    return [
        group
        for group in groups
        if isinstance(group.get("group_type"), str)
        and str(group["group_type"]) in command_loop_group_types
    ]


def repeated_failure_max_repeat_count(groups: list[dict[str, object]]) -> int:
    repeat_counts = [group.get("repeat_count") for group in groups]
    return max((count for count in repeat_counts if isinstance(count, int)), default=0)


def repeated_failure_source_event_ids(groups: list[dict[str, object]]) -> list[str]:
    source_event_ids: set[str] = set()
    for group in groups:
        group_source_event_ids = group.get("source_event_ids", [])
        if isinstance(group_source_event_ids, list):
            source_event_ids.update(
                event_id for event_id in group_source_event_ids if isinstance(event_id, str)
            )
    return sorted(source_event_ids)

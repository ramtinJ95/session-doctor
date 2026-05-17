from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatchFileChange:
    path: str
    operation: str
    added_lines: int = 0
    removed_lines: int = 0


def apply_patch_file_changes(patch_text: str) -> list[PatchFileChange]:
    changes: list[PatchFileChange] = []
    current_index: int | None = None
    for line in patch_text.splitlines():
        path, operation = apply_patch_header(line)
        if path and operation:
            changes.append(PatchFileChange(path=path, operation=operation))
            current_index = len(changes) - 1
            continue
        if current_index is None:
            continue
        current_change = changes[current_index]
        if line.startswith("+") and not line.startswith("+++"):
            changes[current_index] = PatchFileChange(
                path=current_change.path,
                operation=current_change.operation,
                added_lines=current_change.added_lines + 1,
                removed_lines=current_change.removed_lines,
            )
        elif line.startswith("-") and not line.startswith("---"):
            changes[current_index] = PatchFileChange(
                path=current_change.path,
                operation=current_change.operation,
                added_lines=current_change.added_lines,
                removed_lines=current_change.removed_lines + 1,
            )
    return changes


def apply_patch_header(line: str) -> tuple[str | None, str | None]:
    header_prefixes = {
        "*** Add File: ": "write",
        "*** Delete File: ": "delete",
        "*** Update File: ": "update",
    }
    for prefix, operation in header_prefixes.items():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip(), operation
    if line.startswith("diff --git "):
        parts = line.split()
        if len(parts) >= 4:
            return strip_diff_prefix(parts[3]), "update"
    return None, None


def strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path

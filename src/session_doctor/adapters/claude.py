from __future__ import annotations

from pathlib import Path

from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, SessionSource, SourceKind

from .base import BaseAdapter


class ClaudeCodeAdapter(BaseAdapter):
    name = AgentName.CLAUDE
    display_name = "Claude Code"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".claude" / "projects",)

    def discover(self, root: Path | None = None) -> list[SessionSource]:
        discovery_root = self.root_for_discovery(root)
        if not discovery_root.exists():
            return []

        return [
            self._source_for_path(path, discovery_root)
            for path in sorted(discovery_root.rglob("*"))
            if path.is_file()
        ]

    def _source_for_path(self, path: Path, root: Path) -> SessionSource:
        source_kind = classify_claude_path(path, root)
        return SessionSource(
            source_id=source_id_for_path(self.name, path),
            agent_name=self.name,
            source_path=str(path),
            source_kind=source_kind,
            metadata={
                "relative_path": str(path.relative_to(root)),
                "ignored": source_kind == SourceKind.AUXILIARY,
            },
        )


def classify_claude_path(path: Path, root: Path | None = None) -> SourceKind:
    relative_path = path.relative_to(root) if root and path.is_relative_to(root) else path
    parts = relative_path.parts
    if len(parts) >= 4 and parts[-2] == "tool-results":
        return SourceKind.TOOL_RESULT
    if len(parts) >= 4 and parts[-2] == "subagents" and path.suffix == ".jsonl":
        return SourceKind.SUBSESSION
    if len(parts) >= 4 and parts[-2] == "subagents" and path.suffix == ".json":
        return SourceKind.SUBAGENT_METADATA
    if path.suffix == ".jsonl":
        return SourceKind.ROOT_SESSION
    if path.suffix in {".md", ".txt"}:
        return SourceKind.MEMORY
    return SourceKind.AUXILIARY

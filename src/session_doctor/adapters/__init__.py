from __future__ import annotations

from .base import BaseAdapter, ParsedSessionBundle
from .claude import ClaudeCodeAdapter
from .codex import CodexAdapter
from .errors import RecoverableSourceError, SourceFormatError, SourceReadError
from .pi import PiAdapter


def built_in_adapters() -> tuple[BaseAdapter, ...]:
    return (CodexAdapter(), ClaudeCodeAdapter(), PiAdapter())


__all__ = [
    "BaseAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "ParsedSessionBundle",
    "RecoverableSourceError",
    "SourceFormatError",
    "SourceReadError",
    "PiAdapter",
    "built_in_adapters",
]

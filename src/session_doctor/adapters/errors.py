from __future__ import annotations

from pathlib import Path


class RecoverableSourceError(Exception):
    category = "source_error"

    def __init__(self, source_path: Path, detail: str) -> None:
        self.source_path = source_path
        self.detail = detail
        super().__init__(detail)


class SourceReadError(RecoverableSourceError):
    category = "source_read_error"


class SourceFormatError(RecoverableSourceError):
    category = "source_format_error"

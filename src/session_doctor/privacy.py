from __future__ import annotations

import hashlib
import re
from pathlib import Path

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "auth",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def text_length(text: str | None) -> int:
    return len(text or "")


def redact_home(path: str | Path) -> str:
    raw_path = str(path)
    home = str(Path.home())
    if raw_path == home:
        return "~"
    if raw_path.startswith(f"{home}/"):
        return raw_path.replace(home, "~", 1)
    return raw_path


def looks_sensitive_key(key: str) -> bool:
    normalized_key = key.lower().replace("-", "_")
    return any(part in normalized_key for part in SENSITIVE_KEY_PARTS)


def redact_command_for_display(command: str) -> str:
    redacted_command = command
    for key in SENSITIVE_KEY_PARTS:
        redacted_command = re.sub(
            rf"(?i)({re.escape(key)}[A-Z0-9_ -]*=)(\S+)",
            r"\1<redacted>",
            redacted_command,
        )
    home = str(Path.home())
    redacted_command = redacted_command.replace(f"{home}/", "~/")
    return redact_home(redacted_command)

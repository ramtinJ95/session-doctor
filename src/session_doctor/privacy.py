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
    sensitive_key = rf"[A-Z0-9_-]*(?:{'|'.join(SENSITIVE_KEY_PARTS)})[A-Z0-9_-]*"
    value = r"(?:\"[^\"]*\"|'[^']*'|[^\s'\"]+)"
    patterns = (
        (
            r"(?i)([\"']authorization\s*:\s*(?:bearer|basic|token)?\s*)[^\"']*([\"'])",
            r"\1<redacted>\2",
        ),
        (
            rf"(?i)([\"']{sensitive_key}\s*:\s*)[^\"']*([\"'])",
            r"\1<redacted>\2",
        ),
        (
            r"(?i)(\bauthorization\s*:\s*(?:bearer|basic|token)\s+)[^'\"|;&]+",
            r"\1<redacted>",
        ),
        (r"(?i)(\bbearer\s+)[^'\"|;&]+", r"\1<redacted>"),
        (rf"(?i)(\b{sensitive_key}\s*=\s*){value}", r"\1<redacted>"),
        (rf"(?i)(--?{sensitive_key}\s+){value}", r"\1<redacted>"),
        (rf"(?i)(\b{sensitive_key}\s*:\s*){value}", r"\1<redacted>"),
        (rf"(?i)([?&]{sensitive_key}=){value}", r"\1<redacted>"),
        (r"(?i)([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1<redacted>@"),
    )
    for pattern, replacement in patterns:
        redacted_command = re.sub(pattern, replacement, redacted_command)
    home = str(Path.home())
    redacted_command = redacted_command.replace(f"{home}/", "~/")
    return redact_home(redacted_command)


def display_project_hint(
    project_path: str | None, cwd: str | None
) -> tuple[str | None, str | None]:
    if project_path:
        return redact_home(project_path), "session_project_path"
    if cwd:
        return redact_home(cwd), "session_cwd"
    return None, None


def display_file_path(
    *,
    project_relative_path: str | None,
    normalized_path: str,
    canonical_path: str | None,
) -> str:
    if project_relative_path:
        return project_relative_path
    if normalized_path and not Path(normalized_path).is_absolute():
        return normalized_path
    return redact_home(canonical_path or normalized_path)


def public_fingerprint(kind: str, private_identity: str) -> str:
    from .ids import stable_id

    return stable_id("public-fingerprint", kind, private_identity)

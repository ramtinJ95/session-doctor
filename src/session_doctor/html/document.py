from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .assets import SCRIPT, STYLES
from .components import attr, text

CSP = (
    "default-src 'none'; base-uri 'none'; connect-src 'none'; font-src 'none'; "
    "form-action 'none'; frame-src 'none'; img-src data:; object-src 'none'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'"
)


class HtmlWriteError(RuntimeError):
    pass


class HtmlRenderError(RuntimeError):
    pass


def document(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{attr(CSP)}">\n'
        f"<title>{text(title)}</title>\n"
        f"<style>{STYLES}</style>\n"
        "</head>\n<body>\n"
        '<a class="skip-link" href="#main-content">Skip to report content</a>\n'
        f'<div class="page">{body}</div>\n'
        f"<script>{SCRIPT}</script>\n"
        "</body>\n</html>\n"
    )


def write_html(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except (OSError, UnicodeError) as exc:
        raise HtmlWriteError("HTML output could not be replaced") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

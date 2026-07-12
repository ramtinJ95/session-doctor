from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from html import escape


def text(value: object) -> str:
    return escape(str(value), quote=False)


def attr(value: object) -> str:
    return escape(str(value), quote=True)


def humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def display_value(value: object | None) -> str:
    if value is None:
        return '<span class="muted">Unavailable</span>'
    if isinstance(value, bool):
        return text("Yes" if value else "No")
    if isinstance(value, datetime):
        return f'<time datetime="{attr(value.isoformat())}">{text(value.isoformat(sep=" "))}</time>'
    return text(value)


def code(value: object | None) -> str:
    if value is None:
        return '<span class="muted">Unavailable</span>'
    return f"<code>{text(value)}</code>"


def badge(label: str, status: str) -> str:
    allowed = {
        "current",
        "available",
        "neutral",
        "risk",
        "stale",
        "missing",
        "unavailable",
    }
    css_status = status if status in allowed else "unavailable"
    return f'<span class="badge {css_status}">{text(label)}</span>'


def card(body: str, *, heading: str | None = None, css_class: str = "") -> str:
    title = f"<h3>{text(heading)}</h3>" if heading is not None else ""
    classes = "card" + (f" {attr(css_class)}" if css_class else "")
    return f'<article class="{classes}">{title}{body}</article>'


def definition_list(rows: Iterable[tuple[str, object | None, bool]]) -> str:
    content = "".join(
        f"<dt>{text(label)}</dt><dd>{code(value) if is_code else display_value(value)}</dd>"
        for label, value, is_code in rows
    )
    return f'<dl class="meta">{content}</dl>'


def table(headers: list[str], rows: Iterable[list[str]], *, caption: str | None = None) -> str:
    caption_markup = f"<caption>{text(caption)}</caption>" if caption else ""
    header_markup = "".join(f'<th scope="col">{text(header)}</th>' for header in headers)
    row_markup = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        '<div class="table-wrap"><table>'
        f"{caption_markup}<thead><tr>{header_markup}</tr></thead><tbody>{row_markup}</tbody>"
        "</table></div>"
    )


def empty_state(message: str) -> str:
    return f'<p class="empty">{text(message)}</p>'


def disclosure(title: str, body: str, *, open_by_default: bool = False) -> str:
    open_attribute = " open" if open_by_default else ""
    return (
        f"<details{open_attribute}><summary>{text(title)}</summary>"
        f'<div class="details-body">{body}</div></details>'
    )


def statement_list(rows: Iterable[tuple[str, str, list[str]]]) -> str:
    items = []
    for row_code, summary, evidence_ids in rows:
        evidence = ""
        if evidence_ids:
            evidence = (
                '<br><span class="muted">Evidence: '
                + ", ".join(code(item) for item in evidence_ids)
                + "</span>"
            )
        items.append(f"<li><strong>{code(row_code)}</strong>: {text(summary)}{evidence}</li>")
    return '<ul class="clean">' + "".join(items) + "</ul>" if items else empty_state("None")

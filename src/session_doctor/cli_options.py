from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from .adapters import BaseAdapter, built_in_adapters
from .config import default_database_path
from .schemas.common import AgentName
from .schemas.sessions import SessionSource
from .store import DuckDBStore, SchemaMismatchError
from .store.models import SummaryFilters

console = Console()


def os_access_writable(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def path_can_be_created(path: Path) -> bool:
    current_path = path.expanduser()
    while not current_path.exists() and current_path != current_path.parent:
        current_path = current_path.parent
    return os_access_writable(current_path)


def database_path_is_valid(path: Path) -> bool:
    expanded_path = path.expanduser()
    if expanded_path.exists() and not expanded_path.is_file():
        return False
    return path_can_be_created(expanded_path.parent)


def database_path_from_option(path: Path | None) -> Path:
    return path.expanduser() if path else default_database_path()


def require_valid_database_path(path: Path) -> None:
    if database_path_is_valid(path):
        return
    console.print(f"[red]Invalid database path:[/red] {path}")
    raise typer.Exit(1)


def require_existing_database_path(path: Path) -> None:
    if path.exists():
        return
    console.print(f"[red]Database does not exist:[/red] {path}")
    raise typer.Exit(1)


def require_current_database_schema(path: Path, *, allow_empty: bool = False) -> None:
    if not path.exists():
        return
    try:
        DuckDBStore(path).validate_schema(allow_empty=allow_empty)
    except SchemaMismatchError as exc:
        console.print(f"[red]Incompatible database:[/red] {path} ({exc})")
        console.print(f"Delete it and recreate it with: session-doctor db init --db {path}")
        raise typer.Exit(1) from exc


def require_analysis_output_format(output_format: str) -> None:
    if output_format in {"terminal", "json"}:
        return
    console.print("[red]Invalid --format:[/red] expected terminal or json")
    raise typer.Exit(2)


def require_summary_output_format(output_format: str) -> None:
    if output_format in {"terminal", "json"}:
        return
    console.print("[red]Invalid --format:[/red] expected terminal or json")
    raise typer.Exit(2)


def summary_filters_from_options(
    agent: str | None,
    project: Path | None,
    limit: int,
) -> SummaryFilters:
    if limit < 1:
        console.print("[red]Invalid --limit:[/red] expected a positive integer")
        raise typer.Exit(2)

    agent_name = None
    if agent is not None:
        try:
            parsed_agent_name = AgentName(agent)
        except ValueError:
            console.print(f"[red]Unsupported --agent:[/red] {agent}")
            raise typer.Exit(2) from None
        if parsed_agent_name is AgentName.UNKNOWN:
            console.print(f"[red]Unsupported --agent:[/red] {agent}")
            raise typer.Exit(2)
        agent_name = parsed_agent_name.value

    project_path = None
    if project is not None:
        expanded_project = project.expanduser()
        if expanded_project.is_absolute():
            project_path = os.path.normpath(str(expanded_project))
        else:
            project_path = os.path.normpath(str(Path.cwd() / expanded_project))

    return SummaryFilters(agent_name=agent_name, project_path=project_path, limit=limit)


def adapter_for_ingest(agent: str) -> BaseAdapter:
    adapters_by_name = {adapter.name.value: adapter for adapter in built_in_adapters()}
    adapter = adapters_by_name.get(agent)
    if adapter is None:
        console.print(f"[red]Unsupported --agent:[/red] {agent}")
        raise typer.Exit(2)
    return adapter


def sources_for_ingest(adapter: BaseAdapter, source: Path | None) -> list[SessionSource]:
    if source is None:
        return [
            discovered_source
            for discovered_source in adapter.discover()
            if discovered_source.source_kind in adapter.ingestible_source_kinds
        ]

    expanded_source = source.expanduser()
    if not expanded_source.exists():
        console.print(f"[red]Source does not exist:[/red] {expanded_source}")
        raise typer.Exit(1)

    resolved_source = expanded_source.resolve()
    if resolved_source.is_dir():
        return [
            discovered_source
            for discovered_source in adapter.discover(resolved_source)
            if discovered_source.source_kind in adapter.ingestible_source_kinds
        ]
    if resolved_source.is_file():
        session_source = adapter.source_for_path(resolved_source)
        if session_source.source_kind not in adapter.ingestible_source_kinds:
            console.print(
                f"[red]Unsupported source kind for {adapter.display_name} ingestion:[/red] "
                f"{session_source.source_kind.value}"
            )
            raise typer.Exit(2)
        return [session_source]

    console.print(f"[red]Source is not a file or directory:[/red] {resolved_source}")
    raise typer.Exit(1)

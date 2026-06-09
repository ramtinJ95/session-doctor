from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from .adapters import BaseAdapter, built_in_adapters
from .config import default_database_path
from .ids import source_id_for_path
from .schemas import SourceKind
from .schemas.common import AgentName
from .schemas.sessions import SessionSource
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
    if adapter.name not in {AgentName.CODEX, AgentName.PI}:
        console.print(f"[red]--agent {agent} is discovered but parsing is not implemented.[/red]")
        raise typer.Exit(2)
    return adapter


def sources_for_ingest(adapter: BaseAdapter, source: Path | None) -> list[SessionSource]:
    if source is None:
        return adapter.discover()

    expanded_source = source.expanduser()
    if not expanded_source.exists():
        console.print(f"[red]Source does not exist:[/red] {expanded_source}")
        raise typer.Exit(1)

    resolved_source = expanded_source.resolve()
    if resolved_source.is_dir():
        return adapter.discover(resolved_source)
    if resolved_source.is_file():
        return [
            SessionSource(
                source_id=source_id_for_path(adapter.name, resolved_source),
                agent_name=adapter.name,
                source_path=str(resolved_source),
                source_kind=SourceKind.ROOT_SESSION,
            )
        ]

    console.print(f"[red]Source is not a file or directory:[/red] {resolved_source}")
    raise typer.Exit(1)

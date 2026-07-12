from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Never

import typer
from rich.console import Console

from .adapters import BaseAdapter, built_in_adapters
from .config import default_database_path
from .schemas.common import AgentName, SourceKind
from .schemas.sessions import SessionSource
from .store import DatabaseOpenError, DuckDBStore, SchemaMismatchError
from .store.models import SessionScopeFilters, StoreInfo, SummaryFilters
from .store.trend_models import ProjectFilters, TrendBucketSize, TrendFilters

console = Console()


@dataclass(frozen=True)
class SourceSelection:
    sources: tuple[SessionSource, ...]
    discovered_counts: dict[str, int]


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
    except DatabaseOpenError as exc:
        raise_invalid_database(path, exc)
    except SchemaMismatchError as exc:
        console.print(f"[red]Incompatible database:[/red] {path} ({exc})")
        console.print(f"Delete it and recreate it with: session-doctor db init --db {path}")
        raise typer.Exit(1) from exc


def database_info_for_path(path: Path) -> StoreInfo:
    try:
        return DuckDBStore(path).info()
    except DatabaseOpenError as exc:
        raise_invalid_database(path, exc)


def raise_invalid_database(path: Path, exc: DatabaseOpenError) -> Never:
    console.print(f"[red]Invalid database:[/red] {path} ({exc})")
    console.print("Choose a valid DuckDB file or remove it and initialize a new database.")
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


def require_report_output_format(output_format: str) -> None:
    if output_format in {"terminal", "markdown", "json", "html"}:
        return
    console.print("[red]Invalid --format:[/red] expected terminal, markdown, json, or html")
    raise typer.Exit(2)


def report_output_path_from_options(
    output_format: str,
    output: Path | None,
) -> Path | None:
    if output_format != "html":
        if output is not None:
            console.print("[red]Invalid --output:[/red] only supported with --format html")
            raise typer.Exit(2)
        return None
    if output is None:
        console.print("[red]Missing --output:[/red] required with --format html")
        raise typer.Exit(2)
    destination = output.expanduser()
    if destination.suffix.lower() not in {".html", ".htm"}:
        console.print("[red]Invalid --output:[/red] expected an .html or .htm file")
        raise typer.Exit(2)
    try:
        if not destination.parent.exists() or not destination.parent.is_dir():
            console.print("[red]Invalid --output:[/red] parent directory does not exist")
            raise typer.Exit(2)
        if not os_access_writable(destination.parent):
            console.print("[red]Invalid --output:[/red] parent directory is not writable")
            raise typer.Exit(2)
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            console.print("[red]Invalid --output:[/red] destination must be a regular file")
            raise typer.Exit(2)
    except OSError:
        console.print("[red]Invalid --output:[/red] destination could not be inspected")
        raise typer.Exit(2) from None
    return destination


def require_graph_output_format(output_format: str) -> None:
    if output_format == "json":
        return
    console.print("[red]Invalid --format:[/red] expected json")
    raise typer.Exit(2)


def require_positive_limit(limit: int) -> None:
    if limit > 0:
        return
    console.print("[red]Invalid --limit:[/red] expected a positive integer")
    raise typer.Exit(2)


def summary_filters_from_options(
    agent: str | None,
    project: Path | None,
    limit: int,
) -> SummaryFilters:
    if limit < 1:
        console.print("[red]Invalid --limit:[/red] expected a positive integer")
        raise typer.Exit(2)

    scope_filters = scope_filters_from_options(agent, project)
    return SummaryFilters(
        agent_name=scope_filters.agent_name,
        project_path=scope_filters.project_path,
        limit=limit,
    )


def scope_filters_from_options(
    agent: str | None,
    project: Path | None,
) -> SessionScopeFilters:
    agent_name = agent_name_from_option(agent)

    project_path = None
    if project is not None:
        expanded_project = project.expanduser()
        if expanded_project.is_absolute():
            project_path = os.path.normpath(str(expanded_project))
        else:
            project_path = os.path.normpath(str(Path.cwd() / expanded_project))

    return SessionScopeFilters(agent_name=agent_name, project_path=project_path)


def agent_name_from_option(agent: str | None) -> str | None:
    if agent is None:
        return None
    try:
        parsed_agent_name = AgentName(agent)
    except ValueError:
        console.print(f"[red]Unsupported --agent:[/red] {agent}")
        raise typer.Exit(2) from None
    if parsed_agent_name is AgentName.UNKNOWN:
        console.print(f"[red]Unsupported --agent:[/red] {agent}")
        raise typer.Exit(2)
    return parsed_agent_name.value


def trend_filters_from_options(
    agent: str | None,
    project: Path | None,
    bucket: str,
    periods: int,
    limit: int,
) -> TrendFilters:
    try:
        bucket_size = TrendBucketSize(bucket)
    except ValueError:
        console.print("[red]Invalid --bucket:[/red] expected week or month")
        raise typer.Exit(2) from None
    if periods < 1 or periods > 120:
        console.print("[red]Invalid --periods:[/red] expected an integer from 1 to 120")
        raise typer.Exit(2)
    if limit < 1:
        console.print("[red]Invalid --limit:[/red] expected a positive integer")
        raise typer.Exit(2)
    scope = scope_filters_from_options(agent, project)
    return TrendFilters(
        agent_name=scope.agent_name,
        project_path=scope.project_path,
        bucket=bucket_size,
        periods=periods,
        limit=limit,
    )


def project_filters_from_options(agent: str | None, limit: int) -> ProjectFilters:
    if limit < 1:
        console.print("[red]Invalid --limit:[/red] expected a positive integer")
        raise typer.Exit(2)
    scope = scope_filters_from_options(agent, None)
    return ProjectFilters(agent_name=scope.agent_name, limit=limit)


def adapter_for_ingest(agent: str) -> BaseAdapter:
    adapters_by_name = {adapter.name.value: adapter for adapter in built_in_adapters()}
    adapter = adapters_by_name.get(agent)
    if adapter is None:
        console.print(f"[red]Unsupported --agent:[/red] {agent}")
        raise typer.Exit(2)
    return adapter


def sources_for_ingest(adapter: BaseAdapter, source: Path | None) -> list[SessionSource]:
    return list(source_selection_for_ingest(adapter, source).sources)


def source_selection_for_ingest(
    adapter: BaseAdapter,
    source: Path | None,
) -> SourceSelection:
    if source is None:
        discovered = adapter.discover()
        return selection_from_discovered(adapter, discovered)

    expanded_source = source.expanduser()
    if not expanded_source.exists():
        console.print(f"[red]Source does not exist:[/red] {expanded_source}")
        raise typer.Exit(1)

    resolved_source = expanded_source.resolve()
    if resolved_source.is_dir():
        discovered = adapter.discover(resolved_source)
        return selection_from_discovered(adapter, discovered)
    if resolved_source.is_file():
        session_source = adapter.source_for_path(resolved_source)
        if session_source.source_kind not in adapter.ingestible_source_kinds:
            console.print(
                f"[red]Unsupported source kind for {adapter.display_name} ingestion:[/red] "
                f"{session_source.source_kind.value}"
            )
            raise typer.Exit(2)
        return SourceSelection(
            sources=(session_source,),
            discovered_counts={session_source.source_kind.value: 1},
        )

    console.print(f"[red]Source is not a file or directory:[/red] {resolved_source}")
    raise typer.Exit(1)


def selection_from_discovered(
    adapter: BaseAdapter,
    discovered: list[SessionSource],
) -> SourceSelection:
    counts = {source_kind.value: 0 for source_kind in SourceKind}
    for discovered_source in discovered:
        counts[discovered_source.source_kind.value] += 1
    selected = sorted(
        (
            discovered_source
            for discovered_source in discovered
            if discovered_source.source_kind in adapter.ingestible_source_kinds
        ),
        key=lambda candidate: (
            candidate.source_kind is not SourceKind.ROOT_SESSION,
            candidate.source_path,
        ),
    )
    return SourceSelection(
        sources=tuple(selected),
        discovered_counts={key: value for key, value in counts.items() if value},
    )

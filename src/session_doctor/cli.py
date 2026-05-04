from __future__ import annotations

import typer


app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")


@app.callback()
def main() -> None:
    pass

"""
Orka CLI — AI-Powered Semantic Code Surgery.

Usage:
    ./orka --help
    ./orka scan
    ./orka inspect --id "File:path/to/file.py"
    ./orka extract --file src.py --cls MyClass --dest dst.py
    ./orka refactor --file src.py --cls MyClass --method my_method --req "new logic"
"""

import os
import logging
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from orka.config import settings
from orka.orchestrator import Orchestrator
from orka.core.ingester import OrkaGraphDB
from orka.surgery.transplanter import transplant_class
from orka.core.cascade import cascade_import_updates

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
logger = logging.getLogger("orka.cli")
app = typer.Typer(help="Orka: AI-Powered Semantic Code Surgery", no_args_is_help=True)
console = Console()
workspace_dir = str(settings.PROJECT_ROOT)
_SCAN_LOCK_FILE = os.path.join(workspace_dir, ".orka_scan.lock")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_scan_running() -> bool:
    """Check if a background scan is already running via PID lock file."""
    if not os.path.exists(_SCAN_LOCK_FILE):
        return False
    try:
        with open(_SCAN_LOCK_FILE) as f:
            pid = int(f.read().strip())
        # Check if process with this PID actually exists
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        # Stale lock file — clean it up
        try:
            os.remove(_SCAN_LOCK_FILE)
        except OSError:
            pass
        return False


def _bg_scan() -> None:
    """Kick off a background scan if AUTO_SCAN_AFTER_MUTATION is enabled."""
    if not settings.AUTO_SCAN_AFTER_MUTATION:
        return
    if _is_scan_running():
        console.print("[dim]Background scan already running, skipping.[/dim]")
        return

    console.print("[dim]Triggering background graph scan...[/dim]")
    proc = subprocess.Popen(
        [sys.executable, "-m", "orka.cli", "scan"],
        cwd=workspace_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Write PID lock file
    try:
        with open(_SCAN_LOCK_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def scan() -> None:
    """Scan the codebase, build the dependency graph and ChromaDB vectors."""
    console.print("[bold green]Waking up Orka Brain...[/bold green]")
    Orchestrator(workspace_dir)
    # Clean up lock file if this was a background scan
    try:
        if os.path.exists(_SCAN_LOCK_FILE):
            os.remove(_SCAN_LOCK_FILE)
    except OSError:
        pass
    console.print("[bold green]Graph Brain and ChromaDB vectors are fully synced.[/bold green]")


@app.command()
def inspect(node_id: str = typer.Option(..., "--id", help="Graph Node ID")) -> None:
    """Inspect a graph node and its connections."""
    graph_db = OrkaGraphDB(cache_file=os.path.join(workspace_dir, ".orka_cache.json"))

    if not graph_db.graph.has_node(node_id):
        console.print(f"[bold red]Node '{node_id}' not found in graph.[/bold red]")
        raise typer.Exit(code=1)

    node_data = graph_db.graph.nodes[node_id]
    console.print(f"[bold yellow]Node:[/bold yellow] {node_id}")
    console.print(f"[bold yellow]Type:[/bold yellow]  {node_data.get('node_type', 'unknown')}")
    console.print(f"[bold yellow]File:[/bold yellow]  {node_data.get('file_path', 'unknown')}")
    console.print()

    # Inward edges (what this node depends on)
    predecessors = list(graph_db.graph.predecessors(node_id))
    if predecessors:
        console.print("[bold yellow]Inward Edges (depends on):[/bold yellow]")
        for pred in predecessors:
            edge_data = graph_db.graph.get_edge_data(pred, node_id)
            console.print(f"  ← {pred}  [dim]({edge_data.get('relation', '—')})[/dim]")
    else:
        console.print("[dim]No inward edges.[/dim]")
    console.print()

    # Outward edges (what depends on this node)
    successors = list(graph_db.graph.successors(node_id))
    if successors:
        console.print("[bold yellow]Outward Edges (depended on by):[/bold yellow]")
        for succ in successors:
            edge_data = graph_db.graph.get_edge_data(node_id, succ)
            console.print(f"  → {succ}  [dim]({edge_data.get('relation', '—')})[/dim]")
    else:
        console.print("[dim]No outward edges.[/dim]")


@app.command()
def extract(
    file: str = typer.Option(..., "--file", help="Source file path (relative to project root)"),
    cls: str = typer.Option(..., "--cls", help="Class name to extract"),
    dest: str = typer.Option(..., "--dest", help="Destination file path (relative to project root)"),
) -> None:
    """Extract a class from one file into a new file, auto-healing imports."""
    abs_source = os.path.join(workspace_dir, file)
    abs_dest = os.path.join(workspace_dir, dest)

    if not os.path.exists(abs_source):
        console.print(f"[bold red]Source file not found: {abs_source}[/bold red]")
        raise typer.Exit(code=1)

    success = transplant_class(abs_source, cls, abs_dest, workspace_dir)
    if not success:
        console.print(f"[bold red]Failed to transplant class '{cls}' from {file}.[/bold red]")
        raise typer.Exit(code=1)

    # Cascade import updates across the codebase
    graph_db = OrkaGraphDB(cache_file=os.path.join(workspace_dir, ".orka_cache.json"))
    updated = cascade_import_updates(graph_db, cls, abs_source, abs_dest, workspace_dir)

    console.print(
        f"[bold green]Successfully transplanted '{cls}' "
        f"to {dest} with {updated} cascade updates.[/bold green]"
    )

    _bg_scan()


@app.command()
def refactor(
    file: str = typer.Option(..., "--file", help="File path (relative to project root)"),
    cls: str = typer.Option(..., "--cls", help="Class name containing the method"),
    method: str = typer.Option(..., "--method", help="Method name to refactor"),
    req: str = typer.Option(..., "--req", help="Business requirements for the new logic"),
    provider: str = typer.Option(
        settings.DEFAULT_PROVIDER,
        "--provider",
        help="LLM provider: openai, deepseek, together_ai, gemini, anthropic, openai_compat",
    ),
) -> None:
    """Surgically refactor a method's body using AI."""
    abs_file = os.path.join(workspace_dir, file)

    if not os.path.exists(abs_file):
        console.print(f"[bold red]File not found: {abs_file}[/bold red]")
        raise typer.Exit(code=1)

    orchestrator = Orchestrator(workspace_dir, provider=provider)
    success = orchestrator.refactor_method(abs_file, cls, method, req)

    if success:
        console.print(
            f"[bold green]Successfully refactored {cls}.{method}() in {file}.[/bold green]"
        )
    else:
        console.print(
            f"[bold red]Failed to refactor {cls}.{method}() in {file}.[/bold red]"
        )
        raise typer.Exit(code=1)

    _bg_scan()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()

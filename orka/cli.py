"""
Orka CLI — AI-Powered Semantic Code Surgery.

Usage:
    ./orka --help
    ./orka init
    ./orka init --continue-dev --provider together_ai
    ./orka scan
    ./orka inspect --id "File:path/to/file.py"
    ./orka extract --file src.py --cls MyClass --dest dst.py
    ./orka refactor --file src.py --method my_method --req "new logic"
    ./orka refactor --file src.py --cls MyClass --method my_method --req "new logic"
"""

import os
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from orka.config import settings
from orka.orchestrator import Orchestrator
from orka.core.ingester import OrkaGraphDB
from orka.surgery.transplanter import transplant_class
from orka.core.cascade import cascade_import_updates
from orka.core.init_helper import run_init, show_init_notice, save_status

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


def _emit_json(data: dict) -> None:
    """Print *data* as a single line of JSON to stdout."""
    import json as json_mod
    sys.stdout.write(json_mod.dumps(data, default=str) + "\n")
    sys.stdout.flush()


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
def init(
    continue_dev: bool = typer.Option(False, "--continue-dev", help="Target Continue.dev"),
    cursor: bool = typer.Option(False, "--cursor", help="Target Cursor"),
    claude_code: bool = typer.Option(False, "--claude-code", help="Target Claude Code"),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="Default LLM provider",
    ),
    force: bool = typer.Option(False, "--force", help="Re-write rules even if already set"),
) -> None:
    """Configure Orka for your AI coding tool of choice."""
    # Map flags to editor string
    editor = None
    if continue_dev:
        editor = "continue-dev"
    elif cursor:
        editor = "cursor"
    elif claude_code:
        editor = "claude-code"

    success = run_init(editor=editor, provider=provider, force=force)
    if not success:
        raise typer.Exit(code=1)


@app.command()
def scan() -> None:
    """Scan the codebase, build the dependency graph and ChromaDB vectors."""
    show_init_notice(console, "scan")
    console.print("[bold green]Waking up Orka Brain...[/bold green]")
    Orchestrator(workspace_dir)

    # Update last_scan timestamp in status
    save_status({"last_scan": datetime.now(timezone.utc).isoformat()})

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
    show_init_notice(console, f'inspect --id "{node_id}"')
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
    show_init_notice(console, f'extract --file {file} --cls {cls} --dest {dest}')
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
    cls: Optional[str] = typer.Option(None, "--cls", help="Class name containing the method (omit for standalone functions)"),
    func: Optional[str] = typer.Option(None, "--func", help="Alias for --cls when refactoring a standalone function (mutually exclusive with --cls)"),
    method: str = typer.Option(..., "--method", help="Method or function name to refactor"),
    req: str = typer.Option(..., "--req", help="Business requirements for the new logic"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON instead of human-readable text"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without modifying the file (implies --json)"),
    provider: str = typer.Option(
        settings.DEFAULT_PROVIDER,
        "--provider",
        help="LLM provider: openai, deepseek, together_ai, gemini, anthropic, openai_compat",
    ),
) -> None:
    """Surgically refactor a method's body using AI.

    For class methods:  orka refactor --file app.py --cls MyClass --method my_method --req "..."
    For standalone:     orka refactor --file app.py --method my_function --req "..."
    or:                 orka refactor --file app.py --func my_function --method my_function --req "..."
    """
    # --func is an alias for --cls; use whichever is provided (error if both)
    if cls and func:
        if json_output:
            _emit_json({"success": False, "error": "--cls and --func are mutually exclusive."})
        else:
            console.print("[bold red]--cls and --func are mutually exclusive.[/bold red]")
        raise typer.Exit(code=1)
    target_cls = cls or func

    if target_cls:
        display_label = f"{target_cls}.{method}"
    else:
        display_label = method

    show_init_notice(console, f'refactor --file {file} --method {method} --req "{req[:50]}..."')
    abs_file = os.path.join(workspace_dir, file)

    if not os.path.exists(abs_file):
        msg = f"File not found: {abs_file}"
        if json_output:
            _emit_json({"success": False, "label": display_label, "file": abs_file, "error": msg})
        else:
            console.print(f"[bold red]{msg}[/bold red]")
        raise typer.Exit(code=1)

    # --dry-run implies --json so the IDE/LLM can parse the preview
    use_json = json_output or dry_run

    orchestrator = Orchestrator(workspace_dir, provider=provider)
    result = orchestrator.refactor_method(
        file_path=abs_file,
        method_name=method,
        requirements=req,
        class_name=target_cls,
        dry_run=dry_run,
    )

    if result.success:
        if use_json:
            _emit_json({
                "success": True,
                "label": result.label,
                "file": result.file_path,
                "diff": result.diff,
                "dry_run": result.dry_run,
            })
        else:
            if dry_run:
                console.print(
                    f"[bold yellow]Dry-run for {display_label}() in {file}:[/bold yellow]"
                )
                console.print(result.diff)
            else:
                console.print(
                    f"[bold green]Successfully refactored {display_label}() in {file}.[/bold green]"
                )
    else:
        if use_json:
            _emit_json({
                "success": False,
                "label": result.label,
                "file": result.file_path,
                "error": result.error,
                "dry_run": result.dry_run,
            })
        else:
            console.print(
                f"[bold red]Failed to refactor {display_label}() in {file}.[/bold red]"
            )
        raise typer.Exit(code=1)

    if not dry_run:
        _bg_scan()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()


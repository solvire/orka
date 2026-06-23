"""
Orka CLI — AI-Powered Semantic Code Surgery.

Usage:
    ./orka --help
    ./orka init
    ./orka init --continue-dev --provider together_ai
    ./orka init --kilo
    ./orka scan
    ./orka inspect --id "File:path/to/file.py"
    ./orka extract --file src.py --cls MyClass --dest dst.py
    ./orka refactor --file src.py --method my_method --req "new logic"
    ./orka refactor --file src.py --cls MyClass --method my_method --req "new logic"
"""

import json
import os
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from orka.clients import OrkaClientFactory
from orka.config import settings
from orka.core.ingester import OrkaGraphDB
from orka.surgery.transplanter import transplant_class
from orka.core.import_injector import cascade_import_updates
from orka.core.init_helper import run_init, show_init_notice, save_status, is_initialized, load_status

# Surgery graph pipeline
from orka.operations.graph import run_surgery

# Prompt compiler engine
from orka.core.compiler import PromptCompiler
from orka.core.templates import PromptTemplate, InjectionPoint
from orka.core.rule_resolver import resolve_rules, BUILTIN_RULES_DIR, PROJECT_RULES_DIRNAME
import yaml

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
logger = logging.getLogger("orka.cli")
app = typer.Typer(help="Orka: AI-Powered Semantic Code Surgery")
console = Console()
workspace_dir = str(settings.PROJECT_ROOT)
_SCAN_LOCK_FILE = os.path.join(workspace_dir, ".orka_scan.lock")


def _get_version() -> str:
    """Return orka version, preferring git tag (with dirty indicator) over package metadata."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("orka-tools")
    except Exception:
        return "unknown"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
) -> None:
    """Orka: AI-Powered Semantic Code Surgery."""
    if version:
        typer.echo(f"orka {_get_version()}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


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
    sys.stdout.write(json.dumps(data, default=str) + "\n")
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
    kilo: bool = typer.Option(False, "--kilo", help="Target Kilo"),
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
    elif kilo:
        editor = "kilo"

    success = run_init(editor=editor, provider=provider, force=force)
    if not success:
        raise typer.Exit(code=1)


@app.command()
def scan() -> None:
    """Scan the codebase, build the dependency graph and ChromaDB vectors."""
    show_init_notice(console, "scan")
    console.print("[bold green]Waking up Orka Brain...[/bold green]")
    graph = OrkaGraphDB(cache_file=os.path.join(workspace_dir, ".orka_cache.json"))
    graph.scan_directory(workspace_dir)

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

    # ── Surgery graph pipeline ────────────────────────────────────────
    result = run_surgery(
        source_file=abs_file,
        method_name=method,
        requirements=req,
        prompt_template_name="refactor",
        class_name=target_cls,
        dry_run=dry_run,
        provider=provider,
    )

    # ── Record feedback for self-hardening ────────────────────────────
    try:
        from orka.core.feedback import record_feedback
        record_feedback(
            operation="refactor",
            method=method,
            file=file,
            success=result.get("is_valid", False),
            iterations=result.get("iteration_count", 0),
            gates_passed=4 if result.get("is_valid") else 0,
            dry_run=dry_run,
            error=result.get("validation_output") if not result.get("is_valid") else None,
        )
    except Exception:
        pass  # feedback is best-effort, never blocks the pipeline

    if result.get("is_valid", False):
        if use_json:
            _emit_json({
                "success": True,
                "label": display_label,
                "file": result.get("target_output_file", abs_file),
                "dry_run": dry_run,
            })
        else:
            console.print(
                f"[bold green]Successfully refactored {display_label}() in {file} "
                f"({result.get('iteration_count', 1)} iterations).[/bold green]"
            )
    else:
        error = result.get("fatal_error") or result.get("validation_output", "Unknown error")
        if use_json:
            _emit_json({
                "success": False,
                "label": display_label,
                "file": abs_file,
                "error": error,
                "dry_run": dry_run,
            })
        else:
            console.print(
                f"[bold red]Failed to refactor {display_label}(): {error}[/bold red]"
            )
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Prompt compiler helpers
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent / "prompts" / "templates"


def _load_template(name: str) -> PromptTemplate:
    """Load a :class:`PromptTemplate` from a YAML file in the templates dir.

    Parameters
    ----------
    name
        Template name (e.g. ``"refactor"``, ``"test"``).  Corresponds to
        ``<name>.yaml`` in :const:`_TEMPLATES_DIR`.

    Returns
    -------
    PromptTemplate
        The deserialised template.

    Raises
    ------
    typer.Exit
        If the file doesn't exist or the YAML is malformed.
    """
    path = _TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        console.print(f"[bold red]Template not found: {name}[/bold red]")
        console.print(f"  Expected at: {path}")
        console.print("  Available templates:")
        for f in sorted(_TEMPLATES_DIR.glob("*.yaml")):
            console.print(f"    - {f.stem}")
        raise typer.Exit(code=1)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        console.print(f"[bold red]YAML parse error in {path}:[/bold red] {e}")
        raise typer.Exit(code=1)

    if not isinstance(data, dict):
        console.print(f"[bold red]Invalid template file: {path}[/bold red]")
        raise typer.Exit(code=1)

    # Convert injection_points strings to enum values
    if "injection_points" in data:
        data["injection_points"] = [InjectionPoint(ip) for ip in data["injection_points"]]

    return PromptTemplate(**data)


# ---------------------------------------------------------------------------
# testgen command
# ---------------------------------------------------------------------------


@app.command(name="testgen")
def testgen(
    file: str = typer.Option(..., "--file", help="Source file path (relative to project root)"),
    cls: Optional[str] = typer.Option(None, "--cls", help="Class name containing the method (omit for standalone functions)"),
    func: Optional[str] = typer.Option(None, "--func", help="Alias for --cls"),
    method: str = typer.Option(..., "--method", help="Method or function name to generate tests for"),
    output: Optional[str] = typer.Option(None, "--output", help="Output file path (relative to project root)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview generated tests without writing to disk"),
    run: bool = typer.Option(False, "--run", help="Run pytest after generating tests"),
    count: int = typer.Option(1, "--n", help="Generate N tests (loop pipeline --n times, appending)"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    provider: str = typer.Option(
        settings.DEFAULT_PROVIDER,
        "--provider",
        help="LLM provider",
    ),
) -> None:
    """Generate pytest tests for a method or function using AI.

    Uses the surgery graph pipeline to generate, validate and write tests.
    With ``--n``, runs the pipeline in a loop: each iteration generates one
    test function and appends it to the output file.

    Examples::

        # Dry-run (prints to stdout)
        orka testgen --file app.py --method process --dry-run

        # Write to a test file
        orka testgen --file app.py --cls OrderController --method process \\
            --output tests/test_processor.py

        # Generate 3 test functions in a loop
        orka testgen --file app.py --method calculate --n 3 --output test_calc.py

        # Generate and run tests
        orka testgen --file app.py --method calculate --output test_calc.py --run
    """
    if cls and func:
        if json_output:
            _emit_json({"success": False, "error": "--cls and --func are mutually exclusive."})
        else:
            console.print("[bold red]--cls and --func are mutually exclusive.[/bold red]")
        raise typer.Exit(code=1)
    target_cls = cls or func

    display_target = f"{target_cls}.{method}" if target_cls else method
    show_init_notice(console, f'testgen --file {file} --method {method}')
    abs_file = os.path.join(workspace_dir, file)

    if not os.path.exists(abs_file):
        msg = f"File not found: {abs_file}"
        if json_output:
            _emit_json({"success": False, "label": display_target, "error": msg})
        else:
            console.print(f"[bold red]{msg}[/bold red]")
        raise typer.Exit(code=1)

    abs_output = os.path.join(workspace_dir, output) if output else None

    # Set test_file_target when --run is used so pytest runs against it
    test_file_target = abs_output if run else None

    all_tests: list[str] = []
    success_count = 0

    for i in range(count):
        if count > 1:
            console.print(f"[dim]Iteration {i+1}/{count} — generating test...[/dim]")

        result = run_surgery(
            source_file=abs_file,
            method_name=method,
            requirements=(
                f"Generate a single pytest test function for {display_target}. "
                f"Test behavior, not implementation. This is iteration {i+1} of {count}."
            ),
            prompt_template_name="test",
            class_name=target_cls,
            target_output_file=abs_output,
            test_file_target=test_file_target,
            dry_run=dry_run,
            provider=provider,
        )

        if result.get("is_valid", False):
            draft = result.get("draft_file_content", "")
            # Extract just the test function body (strip import header)
            lines = draft.splitlines()
            test_lines = [
                l for l in lines
                if not l.startswith("import ") and not l.startswith("from ")
            ]
            test_body = "\n".join(test_lines).strip()
            if test_body:
                all_tests.append(test_body)
                success_count += 1

            if output and not dry_run and success_count > 1:
                # Append subsequent tests to the existing file
                with open(abs_output, "a", encoding="utf-8") as f:
                    f.write(f"\n\n{test_body}\n")
        else:
            if not dry_run:
                err = result.get("validation_output", "unknown error")
                console.print(f"  [yellow]Iteration {i+1} failed: {err[:80]}[/yellow]")

        # ── Record feedback for self-hardening ────────────────────────
        try:
            from orka.core.feedback import record_feedback
            record_feedback(
                operation="test",
                method=method,
                file=file,
                success=result.get("is_valid", False),
                iterations=result.get("iteration_count", 0),
                gates_passed=4 if result.get("is_valid") else 0,
                dry_run=dry_run,
                error=result.get("validation_output") if not result.get("is_valid") else None,
            )
        except Exception:
            pass  # feedback is best-effort

    if count > 1 and not output:
        # No output path — concatenate all generated tests to stdout
        combined = "\n\n".join(all_tests)
        if json_output:
            _emit_json({
                "success": success_count > 0,
                "label": display_target,
                "tests_content": combined,
                "dry_run": dry_run,
                "generated": success_count,
                "attempted": count,
            })
        else:
            console.print(combined)
        return

    if json_output or dry_run:
        combined = "\n\n".join(all_tests) if count > 1 else result.get("draft_file_content", "")
        _emit_json({
            "success": success_count > 0,
            "label": display_target,
            "file": result.get("target_output_file", abs_file) if count == 1 else abs_output,
            "tests_content": combined,
            "dry_run": dry_run,
            "generated": success_count,
            "attempted": count,
        })
    elif output:
        msg = (
            f"[bold green]All {count} tests written to {output} for {display_target}.[/bold green]"
            if success_count == count else
            f"[bold yellow]Generated {success_count}/{count} tests for {display_target} "
            f"in {output} ({count - success_count} failed).[/bold yellow]"
        )
        console.print(msg)
    else:
        combined = "\n\n".join(all_tests)
        console.print(combined)


# ---------------------------------------------------------------------------
# prompt command  (formerly "gen" — shows compiled prompt without invoking LLM)
# ---------------------------------------------------------------------------


@app.command(name="prompt")
def prompt(
    prompt_arg: str = typer.Option(..., "--template", "-t", help="Template name (e.g. 'refactor', 'test')"),
    rule: list[str] = typer.Option([], "--rule", help="Rule name(s) to inject (repeatable)"),
    file: Optional[str] = typer.Option(None, "--file", help="Source file path"),
    cls: Optional[str] = typer.Option(None, "--cls", help="Class name"),
    method: Optional[str] = typer.Option(None, "--method", help="Method or function name"),
    req: Optional[str] = typer.Option(None, "--req", help="Business requirements (used as context)"),
) -> None:
    """Assemble and display a compiled prompt using the graph pipeline.

    Runs the graph through ``gather_context`` and ``compile_prompt`` nodes,
    then prints the fully enriched prompt.  No LLM is invoked — use
    ``testgen`` or ``refactor`` to actually run code generation.

    When ``--file`` (and optionally ``--cls``/``--method``/``--req``) are
    provided, real source extraction and enrichment are performed.

    Examples::

        # Compile the refactor template with placeholders
        orka prompt --template refactor

        # Compile with real source extraction
        orka prompt --template refactor --file app.py --cls OrderController --method process

        # Compile with requirements
        orka prompt --template refactor --file app.py --method calculate \\
            --req "add input validation for negative amounts"

        # Compile the test template with custom rules
        orka prompt --template test --rule use_pytest_raises --rule test_behavior_not_mocks
    """
    # ---- 1. Run gather_context if --file is given ────────────────────
    # Build a minimal state dict for the gather_context and compile_prompt nodes
    abs_file = os.path.join(workspace_dir, file) if file else None
    state: dict[str, Any] = {
        "source_file": abs_file or "",
        "target_output_file": abs_file or "",
        "prompt_template_name": prompt_arg,
        "requirements": req or "Implement the business logic.",
        "target_node_id": f"{cls}.{method}" if cls and method else (method or "unknown"),
        "dry_run": True,
        "max_iterations": 1,
        "provider": settings.DEFAULT_PROVIDER,
        "class_name": cls,
        "method_name": method or "",
        "existing_code": "",
        "class_context": "",
        "similar_examples": [],
        "original_file_backup": None,
        "compiled_prompt": "",
        "compiled_prompt_sections": {},
        "draft_snippet": "",
        "draft_file_content": "",
        "validation_output": "",
        "is_valid": False,
        "original_draft_code": "",
        "test_file_target": None,
        "iteration_count": 0,
        "fatal_error": None,
    }

    # Run gather_context if we have a real file
    if abs_file and os.path.exists(abs_file):
        from orka.operations.controllers import context

        try:
            ctx_result = context.execute(state)
            state.update(ctx_result)
            console.print(f"[dim]  Source extracted: {len(state.get('existing_code', ''))} chars[/dim]")
            if state.get("class_context"):
                console.print(f"[dim]  Class context: {len(state['class_context'])} chars[/dim]")
        except Exception as e:
            console.print(f"[yellow]  Warning: extraction failed ({e}) — using placeholders[/yellow]")
    else:
        console.print(f"[dim]  No source file — using placeholder context[/dim]")

    # ---- 2. Run compile_prompt node ─────────────────────────────────
    from orka.operations.controllers import compiler_node

    try:
        compile_result = compiler_node.execute(state)
        state.update(compile_result)
    except Exception as e:
        console.print(f"[bold red]Compilation failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    final_prompt = state.get("compiled_prompt", "")
    sections = state.get("compiled_prompt_sections", {})

    # ---- 3. Display template info ───────────────────────────────────
    console.print()
    console.print(f"[bold]Template:[/bold] {sections.get('template_name', prompt_arg)}")
    rules = sections.get("rules_resolved", [])
    console.print(f"[bold]Rules resolved:[/bold] {len(rules)}")
    for r_name in rules:
        console.print(f"  [dim]• {r_name}[/dim]")

    sig = sections.get("signature", {})
    if sig and sig.get("name"):
        console.print()
        console.print(f"[bold]Signature:[/bold] {sig['name']}({', '.join(sig.get('params', []))})")
        if sig.get("return_type"):
            console.print(f"  [dim]Returns: {sig['return_type']}[/dim]")
        if sig.get("docblock"):
            console.print(f"  [dim]Docblock: {sig['docblock'][:80]}...[/dim]")
        if sig.get("is_async"):
            console.print(f"  [dim]Async: yes[/dim]")

    graph_summary = sections.get("graph_summary", "")
    if graph_summary:
        console.print(f"  [dim]Graph: {graph_summary}[/dim]")

    # ---- 4. Display the compiled prompt ─────────────────────────────
    console.print()
    console.print("[bold]─" * 50 + "[/bold]")
    console.print("[bold green]COMPILED PROMPT[/bold green]")
    console.print("[bold]─" * 50 + "[/bold]")
    console.print()
    console.print(final_prompt)
    console.print()
    console.print("[bold]─" * 50 + "[/bold]")
    console.print(f"[dim]Total: {len(final_prompt)} characters, ~{len(final_prompt.split())} tokens (est.)[/dim]")


# ---------------------------------------------------------------------------
# doctor command
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Diagnose Orka configuration and project health."""
    if json_output:
        report = {
            "initialized": is_initialized(),
            "provider": settings.DEFAULT_PROVIDER,
            "smart_model": settings.smart_model,
            "fast_model": settings.fast_model,
            "edit_model": settings.edit_model,
            "temperature": settings.TEMPERATURE,
            "timeout": settings.TIMEOUT,
            "max_retries": settings.MAX_RETRIES,
            "verify_ssl": settings.VERIFY_SSL,
            "auto_scan": settings.AUTO_SCAN_AFTER_MUTATION,
            "dry_run": settings.DRY_RUN,
            "verbose": settings.VERBOSE,
            "api_keys": {
                "openai": bool(settings.OPENAI_API_KEY),
                "deepseek": bool(settings.DEEPSEEK_API_KEY),
                "together_ai": bool(settings.TOGETHER_API_KEY),
                "gemini": bool(settings.GEMINI_API_KEY),
                "anthropic": bool(settings.ANTHROPIC_API_KEY),
                "openai_compat": bool(settings.API_KEY),
            },
            "project_root": str(settings.PROJECT_ROOT),
        }

        # Add last_scan timestamp if available
        try:
            status = load_status()
            report["last_scan"] = status.get("last_scan")
        except Exception:
            report["last_scan"] = None

        # ── Health checks ────────────────────────────────────────────
        console.print("[dim]Running provider health checks...[/dim]")
        health_results = {}
        provider_key_map = {
            "openai": settings.OPENAI_API_KEY,
            "deepseek": settings.DEEPSEEK_API_KEY,
            "together_ai": settings.TOGETHER_API_KEY,
            "gemini": settings.GEMINI_API_KEY,
            "anthropic": settings.ANTHROPIC_API_KEY,
            "openai_compat": settings.API_KEY,
        }
        for prov, key in provider_key_map.items():
            if key:
                health_results[prov] = OrkaClientFactory.check_provider_health(prov)
            else:
                health_results[prov] = {"alive": False, "error": "No API key configured", "latency_ms": 0.0}
        report["health_checks"] = health_results

        _emit_json(report)
        return

    # Human-readable output
    console.print("[bold]Orka Doctor — Configuration & Health Check[/bold]")
    console.print()

    # 1. Initialization status
    if is_initialized():
        console.print("[bold green]✓[/bold green] Project is initialized")
    else:
        console.print("[bold yellow]⚠[/bold yellow] Project is not initialized — run [bold]orka init[/bold]")
    console.print()

    # 2. Configuration report
    console.print(settings.report())
    console.print()

    # 3. Last scan timestamp
    try:
        status = load_status()
        last_scan = status.get("last_scan")
        if last_scan:
            console.print(f"[bold]Last graph scan:[/bold] {last_scan}")
        else:
            console.print("[dim]No graph scan has been performed yet.[/dim]")
    except Exception:
        console.print("[dim]Could not read scan status.[/dim]")
    console.print()

    # 4. Graph database health
    graph_db_path = os.path.join(workspace_dir, ".orka_cache.graph.json")
    if os.path.exists(graph_db_path):
        try:
            with open(graph_db_path) as f:
                graph_data = json.load(f)
            node_count = len(graph_data.get("nodes", []))
            edge_count = len(graph_data.get("edges", []))
            console.print(f"[bold]Graph database:[/bold] {node_count} nodes, {edge_count} edges")
        except Exception:
            console.print("[dim]Graph database exists but could not be read.[/dim]")
    else:
        console.print("[dim]No graph database found — run [bold]orka scan[/bold] to build it.[/dim]")
    console.print()

    # 5. Provider health checks
    console.print("[bold]Provider Health Checks:[/bold]")
    provider_key_map = {
        "openai": settings.OPENAI_API_KEY,
        "deepseek": settings.DEEPSEEK_API_KEY,
        "together_ai": settings.TOGETHER_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openai_compat": settings.API_KEY,
    }

    configured_providers = [(prov, key) for prov, key in provider_key_map.items() if key]
    if not configured_providers:
        console.print("  [dim]No API keys configured — run [bold]orka init[/bold] or set environment variables.[/dim]")
    else:
        console.print(f"  [dim]Testing {len(configured_providers)} configured provider(s)...[/dim]")
        for prov, key in configured_providers:
            result = OrkaClientFactory.check_provider_health(prov)
            if result["alive"]:
                console.print(
                    f"  [bold green]✓[/bold green] {prov:20s} "
                    f"[dim]({result['latency_ms']:.0f}ms)[/dim]"
                )
            else:
                console.print(
                    f"  [bold red]✗[/bold red] {prov:20s} "
                    f"[dim]{result['error'][:60]}[/dim]"
                )


# ---------------------------------------------------------------------------
# mcp command — start MCP server for IDE tool integration
# ---------------------------------------------------------------------------


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio transport) for IDE tool integration.

    Configure in your IDE's MCP settings, e.g. for Kilo::

        "mcp": {
            "orka": {
                "type": "local",
                "command": ["env/bin/python", "-m", "orka.mcp.server"],
                "enabled": true
            }
        }

    The server exposes orka_scan, orka_inspect, orka_refactor, orka_testgen,
    orka_extract, and orka_doctor as MCP tools with structured JSON schemas.
    """
    import asyncio

    from orka.mcp.server import main as mcp_main

    asyncio.run(mcp_main())


# ---------------------------------------------------------------------------
# feedback command — view collected surgery feedback for upgrades
# ---------------------------------------------------------------------------


@app.command()
def feedback(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries to show"),
) -> None:
    """View feedback collected from surgery runs for upgrade insights.

    Every orka refactor/testgen run logs edge cases, fix-loop iterations,
    and validation failures to .orka/feedback.json. This command surfaces
    patterns that indicate where orka needs improvement.
    """
    from orka.core.feedback import load_feedback, summarize_feedback

    entries = load_feedback()
    if not entries:
        console.print("[dim]No feedback collected yet. Run orka refactor or orka testgen first.[/dim]")
        return

    summary = summarize_feedback(entries[:limit])

    if json_output:
        _emit_json({"summary": summary, "entries": entries[:limit]})
        return

    console.print(Panel.fit("[bold cyan]Orka Surgery Feedback[/bold cyan]", border_style="cyan"))
    console.print()

    console.print(f"[bold]Total runs:[/bold] {summary['total_runs']}")
    console.print(f"[bold]First-try success:[/bold] {summary['first_try_success']} ({summary['first_try_rate']:.0%})")
    console.print(f"[bold]Avg iterations:[/bold] {summary['avg_iterations']:.1f}")
    console.print(f"[bold]Rollbacks:[/bold] {summary['rollbacks']}")
    console.print()

    if summary["common_failures"]:
        console.print("[bold yellow]Common failure patterns:[/bold yellow]")
        for pattern, count in summary["common_failures"].items():
            console.print(f"  {count}x — {pattern}")
        console.print()

    if summary["edge_cases"]:
        console.print("[bold]Edge cases logged:[/bold]")
        for entry in summary["edge_cases"][:10]:
            console.print(
                f"  [{entry['timestamp'][:19]}] "
                f"{entry['operation']}/{entry['method']} — "
                f"iter={entry['iterations']} "
                f"{'✓' if entry['success'] else '✗'} "
                f"{entry.get('note', '')}"
            )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()


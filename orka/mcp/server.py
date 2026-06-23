"""
Orka MCP Server — exposes orka's surgery tools via the Model Context Protocol.

Any MCP-compatible IDE (Kilo, Cursor, Claude Code, Continue, Cline, etc.) can
call these tools directly instead of constructing CLI commands.

Run with: ``orka mcp`` or ``python -m orka.mcp.server``

The server uses stdio transport (standard for MCP). IDEs configure it via
their MCP settings, e.g. for Kilo::

    "mcp": {
        "orka": {
            "type": "local",
            "command": ["env/bin/python", "-m", "orka.mcp.server"],
            "enabled": true
        }
    }
"""

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from orka.config import settings

logger = logging.getLogger("orka.mcp")

# ── Surgery imports (lazy — only loaded when tools are called) ─────────


def _run_surgery_safe(
    file: str,
    method: str,
    req: str,
    cls: str | None = None,
    operation: str = "refactor",
    output: str | None = None,
    dry_run: bool = False,
    run_pytest: bool = False,
    provider: str | None = None,
    n: int = 1,
) -> dict[str, Any]:
    """Run the surgery pipeline and return a structured result.

    Wraps ``orka.operations.graph.run_surgery`` with error handling so MCP
    tool calls never crash the server.
    """
    try:
        from orka.operations.graph import run_surgery

        abs_file = os.path.abspath(file)
        if not os.path.exists(abs_file):
            return {"success": False, "error": f"File not found: {abs_file}"}

        target_node_id = f"{cls}.{method}" if cls else method
        target_output = output or abs_file

        state: dict[str, Any] = {
            "source_file": abs_file,
            "target_output_file": target_output,
            "prompt_template_name": operation,
            "requirements": req,
            "target_node_id": target_node_id,
            "dry_run": dry_run,
            "max_iterations": 3,
            "provider": provider or settings.DEFAULT_PROVIDER,
            "class_name": cls,
            "method_name": method,
            "existing_code": "",
            "class_context": "",
            "similar_examples": [],
            "dependency_signatures": "",
            "original_file_backup": None,
            "draft_snippet": "",
            "draft_file_content": "",
            "validation_output": "",
            "previous_validation_output": "",
            "is_valid": False,
            "original_draft_code": "",
            "test_file_target": target_output if run_pytest else None,
            "compiled_prompt": "",
            "compiled_prompt_sections": {},
            "iteration_count": 0,
            "fatal_error": None,
        }

        results = []
        generated = 0
        attempted = 0

        for i in range(n):
            state["iteration_count"] = 0
            state["is_valid"] = False
            state["fatal_error"] = None
            state["draft_snippet"] = ""
            state["validation_output"] = ""

            final_state = run_surgery(state)
            attempted += 1
            if final_state.get("is_valid"):
                generated += 1
            results.append({
                "iteration": i,
                "success": final_state.get("is_valid", False),
                "error": final_state.get("fatal_error"),
                "validation_output": final_state.get("validation_output", "")[:500],
            })

        return {
            "success": generated > 0,
            "generated": generated,
            "attempted": attempted,
            "results": results,
            "dry_run": dry_run,
            "file": target_output,
        }
    except Exception as e:
        logger.exception("Surgery pipeline error")
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()[-500:]}


def _inspect_safe(node_id: str) -> dict[str, Any]:
    """Inspect a graph node, returning structured data."""
    try:
        from orka.core.ingester import OrkaGraphDB
        from orka.config import settings

        cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
        if not os.path.exists(cache_file):
            return {"error": "No graph cache found. Run 'orka scan' first."}

        graph_db = OrkaGraphDB(cache_file=cache_file)
        node_data = graph_db.graph.nodes.get(node_id)
        if node_data is None:
            return {"error": f"Node '{node_id}' not found in graph."}

        edges = []
        for successor in graph_db.graph.successors(node_id):
            succ_data = graph_db.graph.nodes[successor]
            edge_data = graph_db.graph.edges[node_id, successor]
            edges.append({
                "target": successor,
                "type": edge_data.get("type", ""),
                "target_name": succ_data.get("name", ""),
                "target_type": succ_data.get("node_type", ""),
            })

        return {
            "node_id": node_id,
            "node_type": node_data.get("node_type", ""),
            "name": node_data.get("name", ""),
            "file_path": node_data.get("file_path", ""),
            "lineno": node_data.get("lineno", ""),
            "edges": edges,
        }
    except Exception as e:
        return {"error": str(e)}


def _scan_safe() -> dict[str, Any]:
    """Trigger a codebase scan."""
    try:
        from orka.core.ingester import run_ingestion

        run_ingestion()
        return {"success": True, "message": "Scan complete."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _doctor_safe() -> dict[str, Any]:
    """Run health check."""
    try:
        from orka.config import settings

        return {
            "project_root": str(settings.PROJECT_ROOT),
            "default_provider": settings.DEFAULT_PROVIDER,
            "smart_model": settings.SMART_MODEL,
            "fast_model": settings.FAST_MODEL,
            "edit_model": settings.EDIT_MODEL,
            "temperature": settings.TEMPERATURE,
            "timeout": settings.TIMEOUT,
            "max_retries": settings.MAX_RETRIES,
        }
    except Exception as e:
        return {"error": str(e)}


def _extract_safe(file: str, cls: str, dest: str) -> dict[str, Any]:
    """Extract a class to a new file with import healing."""
    try:
        from orka.surgery.transplanter import transplant_class

        abs_source = os.path.abspath(file)
        abs_dest = os.path.abspath(dest)
        workspace = str(settings.PROJECT_ROOT)

        success = transplant_class(abs_source, cls, abs_dest, workspace)
        if success:
            from orka.core.ingester import OrkaGraphDB
            from orka.core.import_injector import cascade_import_updates

            cache_file = os.path.join(workspace, ".orka_cache.json")
            if os.path.exists(cache_file):
                graph_db = OrkaGraphDB(cache_file=cache_file)
                updated = cascade_import_updates(graph_db, cls, abs_source, abs_dest, workspace)
                return {"success": True, "extracted": cls, "dest": abs_dest, "cascade_updates": updated}
        return {"success": False, "error": f"Could not extract {cls} from {abs_source}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── MCP server setup ───────────────────────────────────────────────────


server = Server("orka")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="orka_scan",
            description="Scan the codebase to build the dependency graph and vector search index. Run this first before using other orka tools, or after major code changes.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="orka_inspect",
            description="Inspect a node in the dependency graph. Returns the node's type, file path, and all edges (dependencies). Use node IDs like 'File:path/to/file.py', 'Class:module.ClassName', 'Method:module.Class.method'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Graph node ID, e.g. 'File:src/app.py' or 'Class:myproject.models.User'",
                    },
                },
                "required": ["node_id"],
            },
        ),
        types.Tool(
            name="orka_refactor",
            description="Surgically refactor a method's body using AI + LibCST. The method signature, decorators, and surrounding code are preserved. The result passes through 4-gate validation (snippet AST, assembly, file AST, pytest) before being written to disk.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Source file path (relative to project root)"},
                    "method": {"type": "string", "description": "Method or function name to refactor"},
                    "req": {"type": "string", "description": "Business requirements for the new logic"},
                    "cls": {"type": "string", "description": "Class name (omit for standalone functions)"},
                    "dry_run": {"type": "boolean", "description": "Preview without modifying files", "default": False},
                    "provider": {"type": "string", "description": "LLM provider override"},
                },
                "required": ["file", "method", "req"],
            },
        ),
        types.Tool(
            name="orka_testgen",
            description="Generate pytest tests for a method or function using AI. Tests pass through 4-gate validation before being written. Can generate multiple tests in a loop.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Source file path"},
                    "method": {"type": "string", "description": "Method or function name to test"},
                    "cls": {"type": "string", "description": "Class name (omit for standalone functions)"},
                    "output": {"type": "string", "description": "Output test file path"},
                    "run": {"type": "boolean", "description": "Run pytest after writing", "default": False},
                    "n": {"type": "integer", "description": "Number of test functions to generate", "default": 1},
                    "dry_run": {"type": "boolean", "description": "Preview without writing", "default": False},
                    "provider": {"type": "string", "description": "LLM provider override"},
                },
                "required": ["file", "method"],
            },
        ),
        types.Tool(
            name="orka_extract",
            description="Extract a class from one file to a new file, automatically healing imports across the entire project using the dependency graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Source file containing the class"},
                    "cls": {"type": "string", "description": "Class name to extract"},
                    "dest": {"type": "string", "description": "Destination file path"},
                },
                "required": ["file", "cls", "dest"],
            },
        ),
        types.Tool(
            name="orka_doctor",
            description="Check orka's configuration health — provider, models, API keys, project root. Use this to diagnose setup issues.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "orka_scan":
        result = _scan_safe()
    elif name == "orka_inspect":
        result = _inspect_safe(arguments.get("node_id", ""))
    elif name == "orka_refactor":
        result = _run_surgery_safe(
            file=arguments["file"],
            method=arguments["method"],
            req=arguments["req"],
            cls=arguments.get("cls"),
            operation="refactor",
            dry_run=arguments.get("dry_run", False),
            provider=arguments.get("provider"),
        )
    elif name == "orka_testgen":
        result = _run_surgery_safe(
            file=arguments["file"],
            method=arguments["method"],
            req=arguments.get("req", f"Generate tests for {arguments['method']}"),
            cls=arguments.get("cls"),
            operation="test",
            output=arguments.get("output"),
            dry_run=arguments.get("dry_run", False),
            run_pytest=arguments.get("run", False),
            n=arguments.get("n", 1),
            provider=arguments.get("provider"),
        )
    elif name == "orka_extract":
        result = _extract_safe(
            file=arguments["file"],
            cls=arguments["cls"],
            dest=arguments["dest"],
        )
    elif name == "orka_doctor":
        result = _doctor_safe()
    else:
        result = {"error": f"Unknown tool: {name}"}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger.info("Starting orka MCP server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

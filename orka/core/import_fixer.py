"""Import resolution for generated test code.

Given a source file path and a target (class or function), produces the
Python ``from ... import ...`` statement needed to reference that target
in a test file.

Two strategies, tried in order:

1. **Graph DB lookup** — requires a scan to have run.  Finds the exact
   ``Class:`` or ``Function:`` node and extracts its dotted module path.
2. **File path heuristic** — always works, no scan required.  Converts
   the source file's relative path into a dotted module path.

Usage::

    from orka.core.import_fixer import resolve_import

    # Simple case — just from a file path
    stmt = resolve_import(
        file_path="/project/src/payments/processor.py",
        class_name="OrderProcessor",
    )
    # → "from src.payments.processor import OrderProcessor\\n"

    # Standalone function
    stmt = resolve_import(
        file_path="/project/app/helpers.py",
        method_name="calculate_discount",
    )
    # → "from app.helpers import calculate_discount\\n"
"""

import os
import logging
from typing import Optional


logger = logging.getLogger(__name__)


def resolve_import(
    file_path: str,
    class_name: Optional[str] = None,
    method_name: Optional[str] = None,
    workspace_dir: str = "",
    graph_db: Optional[object] = None,
) -> Optional[str]:
    """Resolve ``from <module> import <name>`` for the given target.

    Parameters
    ----------
    file_path : str
        Absolute or relative path to the source file containing the target.
    class_name : str, optional
        The class being tested.  Provide this *or* *method_name* (for
        standalone functions).
    method_name : str, optional
        The standalone function being tested.  Only used when *class_name* is
        ``None``.
    workspace_dir : str, optional
        The project root directory.  Required when *file_path* is relative.
    graph_db : OrkaGraphDB, optional
        If provided, the graph DB is queried first for a more reliable
        module path.

    Returns
    -------
    str or None
        Something like ``"from src.payments.processor import OrderProcessor\\n"``,
        or ``None`` if resolution fails (e.g. file doesn't exist).
    """
    # Strategy 1: Graph DB lookup (requires scan)
    if graph_db is not None:
        result = _from_graph(graph_db, class_name, method_name, file_path)
        if result:
            logger.debug("Import resolved via graph DB: %s", result.strip())
            return result

    # Strategy 2: Heuristic from file path (always works)
    result = _from_file_path(file_path, class_name, method_name, workspace_dir)
    if result:
        logger.debug("Import resolved via file path: %s", result.strip())
        return result

    return None


# ---------------------------------------------------------------------------
# Strategy 1 — Graph DB lookup
# ---------------------------------------------------------------------------

def _from_graph(
    graph_db: object,
    class_name: Optional[str],
    method_name: Optional[str],
    file_path: Optional[str],
) -> Optional[str]:
    """Search the graph DB for a matching Class: or Function: node.

    Node IDs look like::

        Class:myapp.models.User
        Function:app.helpers.calculate_discount

    We extract the dotted module path by stripping the type prefix and
    removing the final component (which is the class/function name).
    """
    target_type = "class" if class_name else "function"
    target_name = class_name or method_name

    for node_id, attrs in graph_db.graph.nodes(data=True):
        if attrs.get("node_type") != target_type:
            continue
        if attrs.get("name") != target_name:
            continue
        # If we know the file path, narrow the search
        if file_path and attrs.get("file_path"):
            norm_file = os.path.normpath(file_path)
            norm_attr = os.path.normpath(attrs["file_path"])
            if not norm_file.endswith(norm_attr) and not norm_attr.endswith(norm_file):
                continue

        module_path = _module_from_node_id(node_id)
        if module_path:
            return f"from {module_path} import {target_name}\n"

    return None


def _module_from_node_id(node_id: str) -> Optional[str]:
    """Extract the dotted module path from a graph node ID.

    >>> _module_from_node_id("Class:myapp.models.User")
    "myapp.models"

    >>> _module_from_node_id("Function:app.helpers.calculate_discount")
    "app.helpers"
    """
    if ":" not in node_id:
        return None
    without_type = node_id.split(":", 1)[1]
    parts = without_type.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:-1])


# ---------------------------------------------------------------------------
# Strategy 2 — File path heuristic
# ---------------------------------------------------------------------------

def _from_file_path(
    file_path: str,
    class_name: Optional[str] = None,
    method_name: Optional[str] = None,
    workspace_dir: str = "",
) -> Optional[str]:
    """Convert a file path to a dotted module path.

    Handles absolute paths, relative paths, ``__init__.py`` files, and
    files without a workspace prefix.

    Examples
    --------
    ``/home/project/src/payments/processor.py`` →
    ``from src.payments.processor import OrderProcessor``

    ``/home/project/src/payments/__init__.py`` →
    ``from src.payments import OrderProcessor``
    """
    # Normalise
    path = os.path.normpath(file_path)
    if not path:
        return None

    # Strip workspace prefix if present
    if workspace_dir:
        ws = os.path.normpath(workspace_dir)
        if path.startswith(ws):
            path = path[len(ws):].lstrip("/").lstrip("\\")

    # Remove .py extension
    if path.endswith(".py"):
        path = path[:-3]
    # Handle __init__.py → parent directory
    if path.endswith("/__init__") or path.endswith("\\__init__"):
        path = os.path.dirname(path)

    # Convert slashes to dots
    module_path = path.replace("/", ".").replace("\\", ".")
    # Strip leading dot if any
    module_path = module_path.lstrip(".")

    import_name = class_name or method_name
    if not import_name or not module_path:
        return None

    return f"from {module_path} import {import_name}\n"

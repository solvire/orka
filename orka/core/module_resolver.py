import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

def node_id_to_module(node_id: str) -> Optional[str]:
    """Extract the dotted module path from a graph node ID.

    Handles three node types:
    - ``Class:myapp.models.User`` -> ``"myapp.models"``
    - ``Function:app.helpers.calculate_discount`` -> ``"app.helpers"``
    - ``Method:orka.core.compiler.PromptCompiler.compile`` -> ``"orka.core.compiler"``

    Returns ``None`` when extraction is impossible (no colon, empty path,
    single-part path, etc.).
    """
    if ":" not in node_id:
        return None
    without_type = node_id.split(":", 1)[1]

    # Guard: empty after type prefix
    if not without_type:
        return None

    parts = without_type.split(".")

    # A valid module path has at least 2 parts: module.obj
    if len(parts) < 2:
        return None

    # Method nodes are "module.ClassName.method" — strip last 2 parts
    if node_id.startswith("Method:"):
        if len(parts) < 3:
            return None
        return ".".join(parts[:-2])

    # Class and Function nodes are "module.ClassName" or "module.func" — strip last part
    stripped = parts[:-1]
    # Guard against leading-dot edge case where stripped is empty
    if not stripped or all(p == "" for p in stripped):
        return None
    return ".".join(stripped)


def file_to_module(file_path: str, base_dir: str = "") -> str:
    """Convert a file path to a dotted Python module path.

    - Strips base_dir prefix if provided
    - Removes .py extension
    - Handles __init__.py -> parent directory
    - Converts path separators to dots

    Examples:
    '/home/project/src/payments/processor.py' with base_dir='/home/project' -> 'src.payments.processor'
    '/home/project/src/payments/__init__.py' with base_dir='/home/project' -> 'src.payments'
    """
    path = os.path.normpath(file_path)
    if not path:
        return ""

    if base_dir:
        ws = os.path.normpath(base_dir)
        if path.startswith(ws):
            path = path[len(ws):].lstrip("/").lstrip("\\")

    if path.endswith(".py"):
        path = path[:-3]
        
    if path.endswith("/__init__") or path.endswith("\\__init__"):
        path = os.path.dirname(path)
        
    # Edge case: if it was just __init__.py
    if path == "__init__":
        path = ""

    module_path = path.replace("/", ".").replace("\\", ".")
    module_path = module_path.lstrip(".")
    
    return module_path

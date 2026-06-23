import os
import libcst as cst
from typing import Optional
import logging

from orka.core.locator import find_class, find_method

logger = logging.getLogger(__name__)


def extract_method_source(file_path: str, target_method: str, target_class: Optional[str] = None) -> Optional[str]:
    """Reads the file and extracts the precise source code of the target method.

    Thin wrapper around :func:`orka.core.locator.find_method` — the CST
    traversal now lives in the locator module (single source of truth).
    Returns the method's source as a string, or ``None`` if not found.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = cst.parse_module(source_code)
    node = find_method(tree, target_class, target_method)
    if node is None:
        return None
    return cst.Module(body=[node]).code


def extract_class_source(file_path: str, target_class: str) -> Optional[str]:
    """Reads the file and extracts the precise source code of the target class.

    Thin wrapper around :func:`orka.core.locator.find_class` — the CST
    traversal now lives in the locator module (single source of truth).
    Returns the class source (including decorators and docstrings) as a
    string, or ``None`` if not found / unreadable.
    """
    if not os.path.exists(file_path):
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    try:
        tree = cst.parse_module(source_code)
        node = find_class(tree, target_class)
        if node is None:
            return None
        return cst.Module(body=[node]).code
    except Exception as e:
        logger.error(f"Failed to extract class {target_class} from {file_path}: {e}")
        return None

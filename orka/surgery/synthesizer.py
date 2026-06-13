import os
import libcst as cst
from typing import Optional
import logging


logger = logging.getLogger(__name__)


class MethodExtractor(cst.CSTVisitor):
    """
    Traverses the CST to find a specific method and extract its exact source code.
    """
    def __init__(self, target_method: str, target_class: Optional[str] = None):
        self.target_class = target_class
        self.target_method = target_method
        self.inside_target_class = False if target_class else True
        self.extracted_source: Optional[str] = None

    def visit_ClassDef(self, node: cst.ClassDef) -> Optional[bool]:
        if self.target_class and node.name.value == self.target_class:
            self.inside_target_class = True
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        if self.target_class and original_node.name.value == self.target_class:
            self.inside_target_class = False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> Optional[bool]:
        if self.inside_target_class and node.name.value == self.target_method:
            # Reconstruct just this node's source code
            self.extracted_source = cst.Module(body=[node]).code
        return False # Stop traversing this branch once found


def extract_method_source(file_path: str, target_method: str, target_class: Optional[str] = None) -> Optional[str]:
    """Reads the file and extracts the precise source code of the target method."""
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = cst.parse_module(source_code)
    extractor = MethodExtractor(target_method, target_class)
    tree.visit(extractor)
    
    return extractor.extracted_source


class ClassExtractor(cst.CSTVisitor):
    """
    Traverses the CST to find a specific class and extract its exact source code,
    including its decorators and docstrings.
    """
    def __init__(self, target_class: str):
        self.target_class = target_class
        self.extracted_source: Optional[str] = None

    def visit_ClassDef(self, node: cst.ClassDef) -> Optional[bool]:
        if node.name.value == self.target_class:
            # Wrap the single node in a Module to get its clean string representation
            self.extracted_source = cst.Module(body=[node]).code
            return False # Stop traversing, we found it
        return True


def extract_class_source(file_path: str, target_class: str) -> Optional[str]:
    """Reads the file and extracts the precise source code of the target class."""
    if not os.path.exists(file_path):
        return None
        
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    try:
        tree = cst.parse_module(source_code)
        extractor = ClassExtractor(target_class)
        tree.visit(extractor)
        return extractor.extracted_source
    except Exception as e:
        logger.error(f"Failed to extract class {target_class} from {file_path}: {e}")
        return None



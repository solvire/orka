"""Surgical method body replacement using LibCST."""

import textwrap
import libcst as cst
from typing import Optional


class MethodBodyReplacer(cst.CSTTransformer):
    """
    Traverses the CST to find a specific method (optionally inside a specific class)
    and replaces its body block with new code, preserving decorators and signatures.
    """
    def __init__(self, target_method: str, new_body_source: str, target_class: Optional[str] = None):
        self.target_class = target_class
        self.target_method = target_method
        self.inside_target_class = False if target_class else True
        self.modification_successful = False

        # To parse the raw body snippet into a valid CST IndentedBlock,
        # we wrap it in a dummy function definition.
        indented_code = textwrap.indent(textwrap.dedent(new_body_source).strip(), "    ")
        dummy_wrapper = f"def __dummy__():\n{indented_code}"
        
        parsed_dummy = cst.parse_module(dummy_wrapper)
        # Extract just the IndentedBlock (the body) of the dummy function
        self.new_body_node = parsed_dummy.body[0].body

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if self.target_class and node.name.value == self.target_class:
            self.inside_target_class = True
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        if self.target_class and original_node.name.value == self.target_class:
            self.inside_target_class = False
        return updated_node

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        if self.inside_target_class and original_node.name.value == self.target_method:
            self.modification_successful = True
            # Swap the body, keeping the exact signature, async status, decorators, and preceding comments
            return updated_node.with_changes(body=self.new_body_node)
        return updated_node


def apply_llm_patch(file_path: str, target_method: str, new_logic: str, target_class: Optional[str] = None) -> bool:
    """
    Reads a file, surgically replaces a method's body, and writes it back.
    Returns True if successful, False if the target was not found.
    """
    result = preview_patch(file_path, target_method, new_logic, target_class)
    if result is None:
        return False
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(result)
    return True


def preview_patch(file_path: str, target_method: str, new_logic: str, target_class: Optional[str] = None) -> Optional[str]:
    """
    Simulate a surgical patch **in memory** and return the full patched source.

    Works like ``apply_llm_patch`` but never touches the file on disk.
    Returns the full patched source code as a string, or ``None`` if the
    target method/function was not found.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = cst.parse_module(source_code)
    transformer = MethodBodyReplacer(target_method=target_method, new_body_source=new_logic, target_class=target_class)
    modified_tree = tree.visit(transformer)

    if transformer.modification_successful:
        return modified_tree.code
    return None

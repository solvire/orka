import os
import textwrap
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


def build_synthesis_prompt(
    existing_code: str, 
    business_requirements: str, 
    class_context: str = "",
    graph_constraints: str = ""
) -> str:
    """
    Constructs the exact bounded prompt to send to Together AI / DeepSeek.
    Enforces that the LLM only returns the internal body logic.
    """
    prompt = textwrap.dedent(f"""
        You are an elite Python backend architect working on Orka CLI.
        Your task is to write ONLY the internal body logic for a specific method.

        ### STRICT CONSTRAINTS:
        1. DO NOT output the method signature (`def function_name(...):`).
        2. DO NOT output decorators.
        3. DO NOT wrap the output in markdown code blocks like ```python. 
        4. Return ONLY valid Python code at the base indentation level (0 spaces) that will be safely injected inside the existing method.
        
        ### GRAPH DEPENDENCY CONSTRAINTS (DO NOT BREAK THESE):
        {graph_constraints if graph_constraints else "None."}
        
        ### CLASS CONTEXT:
        {class_context if class_context else "None provided."}

        ### EXISTING METHOD SIGNATURE & CODE:
        ```python
        {existing_code.strip()}
        ```

        ### NEW BUSINESS REQUIREMENTS:
        {business_requirements}

        ### SYNTHESIZED BODY LOGIC (RAW PYTHON ONLY):
    """).strip()
    
    return prompt


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


def build_testgen_prompt(
    existing_code: str,
    class_context: str = "",
    file_path: str = "",
) -> str:
    """Construct a prompt asking the LLM to write pytest tests for a method.

    The LLM is asked to output ONLY test function bodies — no imports,
    no module docstrings, no markdown fences.  The orchestrator prepends
    the necessary ``import pytest`` and ``from ... import ...`` statements
    after generation.

    Parameters
    ----------
    existing_code : str
        The extracted source code of the method or function to test.
    class_context : str, optional
        The full class body (if the target is a class method) for context.
    file_path : str, optional
        The source file path, included so the LLM understands the module
        structure.

    Returns
    -------
    str
        The formatted prompt string.
    """
    prompt = textwrap.dedent(f"""
        You are a pytest specialist working on a Python codebase.
        Your task is to write comprehensive pytest tests for the method or
        function shown below.

        ### STRICT CONSTRAINTS:
        1. Output ONLY raw Python test functions — no imports, no module
           docstrings, no markdown fences like ```python.
        2. Use descriptive test function names following the pattern
           ``test_<method>_<scenario>``.
        3. Cover: happy path, edge cases, error conditions.
        4. Use ``pytest.raises(...)`` for expected exceptions.
        5. Do NOT include any import statements — they will be added
           automatically.
        6. Use ``pytest.approx()`` for float comparisons.
        7. Include docstrings on each test function explaining the scenario.

        ### CLASS CONTEXT (for understanding the class structure):
        {class_context if class_context else "None provided — this is a standalone function."}

        ### SOURCE FILE:
        {file_path}

        ### METHOD/FUNCTION TO TEST:
        ```python
        {existing_code.strip()}
        ```

        ### PYTHON TEST FUNCTIONS (RAW PYTHON ONLY):
    """).strip()
    return prompt
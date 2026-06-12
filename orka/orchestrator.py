"""Orchestrator — The central controller for Orka's refactoring pipeline."""

import difflib
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

# Core Orka Tools
from orka.core.ingester import OrkaGraphDB
from orka.core.validator import validate_code_snippet, validate_file
from orka.surgery.synthesizer import (
    extract_method_source,
    extract_class_source,
    build_synthesis_prompt,
)
from orka.surgery.modifier import apply_llm_patch, preview_patch
from orka.core.import_fixer import resolve_import

# Prompt compiler engine (Phase 2 — Strangler Fig pattern)
from pathlib import Path
from orka.core.compiler import PromptCompiler
from orka.core.templates import PromptTemplate, InjectionPoint
from orka.core.rule_resolver import resolve_rules, BUILTIN_RULES_DIR
import yaml

# Standalone Orka LLM client
from orka.clients import OrkaLangChainClient

logger = logging.getLogger("Orchestrator")

# Path to prompt template YAML files
_TEMPLATES_DIR = Path(__file__).resolve().parent / "prompts" / "templates"


def _load_template(name: str) -> PromptTemplate:
    """Load a :class:`PromptTemplate` from a YAML file in the templates dir."""
    path = _TEMPLATES_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "injection_points" in data:
        data["injection_points"] = [InjectionPoint(ip) for ip in data["injection_points"]]
    return PromptTemplate(**data)


def _target_label(class_name: Optional[str], method_name: str) -> str:
    """Build a human-readable label like 'MyClass.my_method' or 'my_function'."""
    if class_name:
        return f"{class_name}.{method_name}"
    return method_name


@dataclass
class RefactorResult:
    """Structured result from a refactoring operation.

    Attributes:
        success: Whether the refactoring succeeded.
        label: Human-readable name of the refactored target.
        file_path: Absolute path to the modified file.
        diff: Unified diff string showing what changed (empty on failure).
        dry_run: Whether this was a dry run (file not modified).
        error: Human-readable error description (``None`` on success).
    """
    success: bool
    label: str
    file_path: str
    diff: str = ""
    dry_run: bool = False
    error: Optional[str] = None
    tests_content: str = ""


class Orchestrator:
    def __init__(self, workspace_dir: str, provider: str = "together_ai"):
        self.workspace_dir = workspace_dir
        
        # 1. Initialize Dual-Brain (Topology + Semantic)
        self.graph_db = OrkaGraphDB(cache_file=os.path.join(workspace_dir, ".orka_cache.json"))
        logger.info("Initializing Orka Brain...")
        self.graph_db.scan_directory(workspace_dir)
        
        # 2. Initialize the Scalpel (Standalone OrkaLangChainClient)
        logger.info(f"Loading {provider.upper()} client via OrkaLangChainClient...")
        self.llm_client = OrkaLangChainClient(provider=provider)

    def _get_graph_constraints(self, class_name: Optional[str], method_name: str) -> str:
        """Queries NetworkX graph to find incoming connections to warn the LLM.

        Returns a constraints string, or an empty string when *class_name* is
        ``None`` (standalone function) since the graph doesn't track those yet.
        """
        if not class_name:
            return ""

        target_node = None
        for node in self.graph_db.graph.nodes:
            if node.endswith(f".{class_name}.{method_name}") or node.endswith(f".{method_name}"):
                if self.graph_db.graph.nodes[node].get("node_type") == "method":
                    target_node = node
                    break
        
        if not target_node:
            return "No graph constraints found."

        inward_edges = list(self.graph_db.graph.predecessors(target_node))
        if not inward_edges:
            return "No known callers in the internal graph."

        callers = [n.split(":")[-1] for n in inward_edges]
        return f"CRITICAL: This method is called by {len(callers)} other components (e.g., {', '.join(callers[:3])}). DO NOT change the expected return type."

    def refactor_method(
        self,
        file_path: str,
        method_name: str,
        requirements: str,
        class_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> RefactorResult:
        """The Scalpel Pipeline: Uses Direct API to surgically update a method.

        When *class_name* is ``None`` the target is treated as a standalone
        module-level function — no class context is sent to the LLM and the
        graph-constraints lookup is skipped.

        When *dry_run* is ``True`` the file is never modified — the result
        will contain the diff but the patch is only simulated in memory.

        Returns a ``RefactorResult`` with the outcome, diff, and error details.
        """
        label = _target_label(class_name, method_name)
        logger.info(f"Targeting {label} in {file_path}")

        # Capture the file content *before* any changes for diff generation
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                before = f.read()
        except OSError as e:
            return RefactorResult(False, label, file_path, error=str(e))

        # 1. Gather Dependencies
        constraints = self._get_graph_constraints(class_name, method_name)

        # 2. Extract Source
        existing_code = extract_method_source(file_path, method_name, class_name)
        if not existing_code:
            msg = f"Failed to extract source. Does {label} exist?"
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        # 3. Extract Class Context (only for class methods)
        class_context = "" if not class_name else (extract_class_source(file_path, class_name) or "")

        # 4. Build Strict Prompt
        prompt = build_synthesis_prompt(
            existing_code=existing_code,
            business_requirements=requirements,
            class_context=class_context,
            graph_constraints=constraints
        )
        
        system_instruction = "You are a pure code synthesis engine. Output ONLY raw Python code at the base indentation level. Do not include signatures, decorators, or explanations."

        # 5. Invoke LLM Client Directly
        logger.info(f"Invoking {self.llm_client.provider.upper()} API (Scalpel Mode)...")
        try:
            raw_output = self.llm_client.generate_code(
                prompt=prompt,
                system_instruction=system_instruction
            )
            
            # Clean the output using the static method
            clean_logic = OrkaLangChainClient.fix_md_fences(raw_output)
            
        except Exception as e:
            msg = f"LLM Client Synthesis Failed: {e}"
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        # 6. Validate LLM Output Before Patching
        snippet_result = validate_code_snippet(clean_logic, label=label)
        if not snippet_result:
            msg = f"LLM generated invalid code for {label}: {snippet_result.error}"
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        # 7. Apply or Preview Patch
        if dry_run:
            logger.info("Dry-run mode — simulating patch in memory...")
            patched_source = preview_patch(file_path, method_name, clean_logic, class_name)
            if patched_source is None:
                msg = f"LibCST could not find {label} for dry-run patch."
                logger.error(msg)
                return RefactorResult(False, label, file_path, error=msg, dry_run=True)

            # Validate the simulated patched source
            try:
                import ast
                ast.parse(patched_source)
            except SyntaxError as e:
                msg = f"Dry-run patch would introduce syntax error: {e}"
                logger.error(msg)
                return RefactorResult(False, label, file_path, error=msg, dry_run=True)

            after = patched_source
        else:
            logger.info("Applying LibCST Patch...")
            patched = apply_llm_patch(file_path, method_name, clean_logic, class_name)

            if not patched:
                msg = f"LibCST failed to apply patch to {label}. Check indentation or syntax."
                logger.error(msg)
                return RefactorResult(False, label, file_path, error=msg)

            # 8. Validate Patched File on Disk
            file_result = validate_file(file_path)
            if not file_result:
                msg = f"Patch introduced syntax errors in {file_path}: {file_result.error}"
                logger.error(msg)
                # TODO: revert file from backup / git
                return RefactorResult(False, label, file_path, error=msg)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    after = f.read()
            except OSError as e:
                logger.warning(f"Could not read file for diff: {e}")
                after = before

        # 9. Compute diff
        diff = _compute_diff(before, after, file_path)

        if dry_run:
            logger.info(f"Dry-run complete for {label}. File not modified.")
        else:
            logger.info(f"Successfully refactored {label}!")

        return RefactorResult(True, label, file_path, diff=diff, dry_run=dry_run)

    def generate_tests(
        self,
        file_path: str,
        method_name: str,
        class_name: Optional[str] = None,
        output_path: Optional[str] = None,
        dry_run: bool = False,
        run_pytest: bool = False,
    ) -> RefactorResult:
        label = _target_label(class_name, method_name)
        logger.info(f"Generating tests for {label} in {file_path}")

        # 1. Extract Source
        existing_code = extract_method_source(file_path, method_name, class_name)
        if not existing_code:
            msg = f"Failed to extract source. Does {label} exist?"
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        # 2. Extract Class Context (only for class methods)
        class_context = "" if not class_name else (extract_class_source(file_path, class_name) or "")

        # 3. Compile Prompt using PromptCompiler
        template = _load_template("test")
        resolved_rules = resolve_rules(
            template_name=template.name,
            injection_points=template.injection_points,
        )
        # Use a relative path for the prompt to avoid leaking local filesystem structure
        prompt_file_path = file_path
        if self.workspace_dir and file_path.startswith(self.workspace_dir):
            prompt_file_path = os.path.relpath(file_path, self.workspace_dir)
        context_data = {
            "existing_code": existing_code,
            "class_context": class_context,
            "file_path": prompt_file_path,
        }
        compiler = PromptCompiler()
        prompt = compiler.compile(template, resolved_rules, context_data)

        system_instruction = (
            "You are a pytest specialist. Output ONLY raw Python test functions - "
            "no imports, no module docstrings, no markdown fences."
        )

        # 4. Invoke LLM
        logger.info(f"Invoking {self.llm_client.provider.upper()} for test generation...")
        try:
            raw_output = self.llm_client.generate_code(
                prompt=prompt,
                system_instruction=system_instruction
            )
            clean_tests = OrkaLangChainClient.fix_md_fences(raw_output)
        except Exception as e:
            msg = f"LLM Client Failed: {e}"
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        # 5. Validate LLM Output
        snippet_result = validate_code_snippet(clean_tests, label=label)
        if not snippet_result:
            msg = f"LLM generated invalid test code for {label}: {snippet_result.error}"
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        # 6. Build import statement (deterministic, no LLM)
        import_stmt = resolve_import(
            file_path=file_path,
            class_name=class_name,
            method_name=method_name,
            workspace_dir=self.workspace_dir,
            graph_db=self.graph_db,
        )
        if import_stmt is None:
            msg = f"Could not resolve import path for {label}."
            logger.error(msg)
            return RefactorResult(False, label, file_path, error=msg)

        full_test_content = f"import pytest\n{import_stmt}\n{clean_tests}\n"

        # 7. Output handling
        if output_path:
            abs_output = output_path if os.path.isabs(output_path) else os.path.join(self.workspace_dir, output_path)

            if dry_run:
                diff = _compute_diff("", full_test_content, abs_output)
                return RefactorResult(
                    True, label, abs_output,
                    diff=diff, dry_run=True, tests_content=full_test_content,
                )

            os.makedirs(os.path.dirname(abs_output), exist_ok=True)
            try:
                with open(abs_output, "w", encoding="utf-8") as f:
                    f.write(full_test_content)
            except OSError as e:
                return RefactorResult(False, label, abs_output, error=str(e))

            file_result = validate_file(abs_output)
            if not file_result:
                msg = f"Generated test file has syntax errors: {file_result.error}"
                logger.error(msg)
                return RefactorResult(False, label, abs_output, error=msg)

            if run_pytest:
                import subprocess
                import sys
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "pytest", abs_output, "--exitfirst", "--tb=short", "--no-header"],
                        capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        msg = f"pytest failed:\n{result.stdout}\n{result.stderr}"
                        return RefactorResult(False, label, abs_output, error=msg)
                except subprocess.TimeoutExpired:
                    msg = "pytest timed out after 120 seconds."
                    return RefactorResult(False, label, abs_output, error=msg)

            diff = _compute_diff("", full_test_content, abs_output)
            logger.info(f"Tests written to {abs_output}")
            return RefactorResult(True, label, abs_output, diff=diff, tests_content=full_test_content)

        # No output path
        if dry_run:
            return RefactorResult(True, label, file_path, dry_run=True, tests_content=full_test_content)

        return RefactorResult(True, label, file_path, tests_content=full_test_content)


def _compute_diff(before: str, after: str, file_path: str = "") -> str:
    """Return a unified diff string between *before* and *after*."""
    lines = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=file_path,
        tofile=file_path,
    ))
    return "".join(lines)
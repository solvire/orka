import os
import ast
import json
import textwrap
from pathlib import Path
import pytest
import networkx as nx
import logging

# Adjust this import based on your project structure
from orka.core.ingester import OrkaGraphDB, CodeASTVisitor, NodeMetadata

@pytest.fixture
def workspace_dir(tmp_path):
    """
    Creates a temporary workspace with two python files:
    1. A perfectly valid file (will use dynamic inspect).
    2. A file with a broken import (will trigger the AST fallback).
    """
    workspace = tmp_path / "orka_workspace"
    workspace.mkdir()

    # 1. Valid File (Dynamic Inspect Success)
    valid_file = workspace / "valid_controller.py"
    valid_file.write_text(textwrap.dedent("""
        import math

        def calculate_tax():
            return math.pi

        class PaymentController:
            def process_payment(self):
                return calculate_tax()
    """))

    # 2. Broken File (AST Fallback Target)
    broken_file = workspace / "broken_controller.py"
    broken_file.write_text(textwrap.dedent("""
        import nonexistent_django_module  # This will break inspect()!

        class RefundController:
            def process_refund(self):
                pass
    """))

    return workspace


def test_ast_visitor_directly():
    """Unit test for the CodeASTVisitor logic."""
    source_code = textwrap.dedent("""
        from external import tool
        class TestClass:
            def test_method(self):
                pass
        def test_function():
            pass
    """)
    tree = ast.parse(source_code)
    visitor = CodeASTVisitor("dummy.py", "dummy_module", source_code)
    visitor.visit(tree)

    assert len(visitor.classes) == 1
    assert visitor.classes[0]["name"] == "TestClass"
    assert len(visitor.classes[0]["methods"]) == 1
    assert visitor.classes[0]["methods"][0]["name"] == "test_method"
    
    assert len(visitor.functions) == 1
    assert visitor.functions[0]["name"] == "test_function"

    assert len(visitor.imports) == 1
    assert visitor.imports[0]["module"] == "external"


def test_graph_inspect_strategy(workspace_dir):
    """Test that valid Python files are parsed using the dynamic inspect strategy."""
    cache_path = workspace_dir / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    
    valid_file = workspace_dir / "valid_controller.py"
    db._process_file(str(valid_file), "valid_controller.py")

    # Assert Nodes Exist
    file_id = "File:valid_controller.py"
    class_id = "Class:valid_controller.PaymentController"
    method_id = "Method:valid_controller.PaymentController.process_payment"
    func_id = "Function:valid_controller.calculate_tax"

    assert db.graph.has_node(file_id)
    assert db.graph.has_node(class_id)
    assert db.graph.has_node(method_id)
    assert db.graph.has_node(func_id)

    # Assert Metadata is Correct
    assert db.graph.nodes[class_id]["node_type"] == "class"
    assert db.graph.nodes[method_id]["node_type"] == "method"

    # Assert Edges (Topology) are Correct
    assert db.graph.has_edge(file_id, class_id)
    assert db.graph.has_edge(class_id, method_id)
    assert db.graph.edges[class_id, method_id]["relation"] == "CONTAINS"


def test_graph_ast_fallback_strategy(workspace_dir):
    """Test that broken files (Syntax/Import errors) trigger the AST fallback and still graph successfully."""
    cache_path = workspace_dir / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    
    broken_file = workspace_dir / "broken_controller.py"
    db._process_file(str(broken_file), "broken_controller.py")

    # Even though inspect failed due to `nonexistent_django_module`, AST should map it.
    file_id = "File:broken_controller.py"
    class_id = "Class:broken_controller.RefundController"
    method_id = "Method:broken_controller.RefundController.process_refund"

    assert db.graph.has_node(file_id)
    assert db.graph.has_node(class_id)
    assert db.graph.has_node(method_id)

    # Assert Edges
    assert db.graph.has_edge(file_id, class_id)
    assert db.graph.has_edge(class_id, method_id)


def test_directory_scan_and_staleness_caching(workspace_dir, caplog):
    """Test full directory walk, SHA-256 caching, and skip logic."""

    caplog.set_level(logging.INFO)  # Force Pytest to capture our logger output
    
    cache_path = workspace_dir / ".orka_cache.json"
    
    # Run 1: Should process both files
    db_run_1 = OrkaGraphDB(cache_file=str(cache_path))
    db_run_1.scan_directory(str(workspace_dir))
    
    # Check cache was written
    assert cache_path.exists()
    with open(cache_path, "r") as f:
        cache_data = json.load(f)
        assert len(cache_data["hashes"]) == 2  # 2 python files
    
    # Verify graph contains elements from BOTH files
    assert db_run_1.graph.has_node("Class:valid_controller.PaymentController")
    assert db_run_1.graph.has_node("Class:broken_controller.RefundController")

    # Run 2: Should hit the cache and process NOTHING
    db_run_2 = OrkaGraphDB(cache_file=str(cache_path))
    db_run_2.scan_directory(str(workspace_dir))
    
    # Look for the log output proving files were skipped
    assert "Skipped: 2" in caplog.text
    assert "Processed: 0" in caplog.text

    # Modify one file to test cache invalidation
    valid_file = workspace_dir / "valid_controller.py"
    with open(valid_file, "a") as f:
        f.write("\n# Adding a comment to change the file hash\n")

    # Run 3: Should process 1 file and skip 1
    db_run_3 = OrkaGraphDB(cache_file=str(cache_path))
    db_run_3.scan_directory(str(workspace_dir))
    
    assert "Processed: 1" in caplog.text
    assert "Skipped: 1" in caplog.text
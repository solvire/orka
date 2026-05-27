import textwrap
import json
from pathlib import Path
import pytest
import networkx as nx

from orka.core.ingester import OrkaGraphDB

@pytest.fixture
def dual_brain_workspace(tmp_path):
    """Setup a test workspace with a complex python file."""
    workspace = tmp_path / "kidecon_dual"
    workspace.mkdir()
    
    file_path = workspace / "taxes.py"
    original_code = textwrap.dedent("""
        class TaxCalculator:
            def compute_california_tax(self, amount: float):
                \"\"\"Applies the strict California local sales tax logic.\"\"\"
                return amount * 0.0825
                
            def compute_texas_tax(self, amount: float):
                return amount * 0.0625
    """).strip()
    
    file_path.write_text(original_code, encoding="utf-8")
    return workspace

def test_semantic_chromadb_search(dual_brain_workspace):
    """Proves ChromaDB embeddings work and semantic RAG triggers."""
    cache_path = dual_brain_workspace / ".orka_cache.json"
    
    # 1. Ingest the codebase
    db = OrkaGraphDB(cache_file=str(cache_path))
    db.scan_directory(str(dual_brain_workspace))
    
    # 2. Run a semantic search for a concept, not a strict name
    # The string "strict California local sales tax" is in the docstring
    results = db.vector_db.search("California sales tax logic", n_results=1, node_type="method")
    
    assert len(results) == 1
    assert results[0]["id"] == "Method:taxes.TaxCalculator.compute_california_tax"
    assert "0.0825" in results[0]["source"]


def test_graph_disk_persistence_and_ghost_cleanup(dual_brain_workspace):
    """Proves that cached files aren't forgotten, and deleted methods are cleaned up."""
    cache_path = dual_brain_workspace / ".orka_cache.json"
    
    # RUN 1: Initial Ingestion
    db_run_1 = OrkaGraphDB(cache_file=str(cache_path))
    db_run_1.scan_directory(str(dual_brain_workspace))
    
    # Prove the graph persisted to disk
    graph_file = cache_path.with_suffix('.graph.json')
    assert graph_file.exists()
    
    # RUN 2: Reloading from Disk (File hash unchanged)
    db_run_2 = OrkaGraphDB(cache_file=str(cache_path))
    db_run_2.scan_directory(str(dual_brain_workspace))
    
    # Even though file was skipped, the node must exist in memory from disk load
    assert db_run_2.graph.has_node("Method:taxes.TaxCalculator.compute_texas_tax")

    # RUN 3: Modifying the file (Deleting Texas tax)
    file_path = dual_brain_workspace / "taxes.py"
    modified_code = textwrap.dedent("""
        class TaxCalculator:
            def compute_california_tax(self, amount: float):
                return amount * 0.0825
    """).strip()
    file_path.write_text(modified_code, encoding="utf-8")
    
    db_run_3 = OrkaGraphDB(cache_file=str(cache_path))
    db_run_3.scan_directory(str(dual_brain_workspace))
    
    # Texas tax MUST be gone (Ghost Cleanup test)
    assert not db_run_3.graph.has_node("Method:taxes.TaxCalculator.compute_texas_tax")
    
    # But California tax must remain
    assert db_run_3.graph.has_node("Method:taxes.TaxCalculator.compute_california_tax")

    # --- NEW: CHROMADB EMBEDDING CHECKS ---
    # Search for the deleted method concept. It should return NOTHING (or return the Cali one because of vector math, but definitely NOT the Texas ID).
    texas_search = db_run_3.vector_db.search("texas tax", n_results=1)
    if texas_search:
        assert texas_search[0]["id"] != "Method:taxes.TaxCalculator.compute_texas_tax"

    # Search for the kept method to ensure its embedding survived/regenerated
    cali_search = db_run_3.vector_db.search("california tax", n_results=1)
    assert cali_search[0]["id"] == "Method:taxes.TaxCalculator.compute_california_tax"
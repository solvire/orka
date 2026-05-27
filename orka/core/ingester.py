import os
import ast
import json
import hashlib
import inspect
import logging
import importlib.util
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Set
import sys

import networkx as nx

from orka.core.vector_store import OrkaVectorDB

logging.basicConfig(level=logging.INFO, format='%(levelname)s: [%(name)s] %(message)s')
logger = logging.getLogger("Ingestion")

@dataclass
class NodeMetadata:
    name: str
    node_type: str 
    file_path: str
    lineno: Optional[int] = None
    returns: Optional[str] = None
    docstring: Optional[str] = None

class CodeASTVisitor(ast.NodeVisitor):
    """Fallback AST visitor that now extracts physical source code for ChromaDB."""
    def __init__(self, file_path: str, module_name: str, full_source: str):
        self.file_path = file_path
        self.module_name = module_name
        self.full_source = full_source
        self.classes = []
        self.functions = []
        self.imports = []
        self._current_class = None

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append({"module": alias.name, "name": alias.name})
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            self.imports.append({"module": module, "name": alias.name})
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
        source = ast.get_source_segment(self.full_source, node) or ""
        class_info = {
            "name": node.name,
            "lineno": node.lineno,
            "bases": bases,
            "source": source,
            "methods": []
        }
        self.classes.append(class_info)
        
        prev_class = self._current_class
        self._current_class = class_info
        self.generic_visit(node)
        self._current_class = prev_class

    def visit_FunctionDef(self, node):
        source = ast.get_source_segment(self.full_source, node) or ""
        func_info = {
            "name": node.name,
            "lineno": node.lineno,
            "source": source,
            "is_method": self._current_class is not None
        }
        if self._current_class:
            self._current_class["methods"].append(func_info)
        else:
            self.functions.append(func_info)
        # Do not generic_visit(node) to avoid picking up nested functions for now

class OrkaGraphDB:
    def __init__(self, cache_file: str = ".orka_cache.json"):
        self.cache_file = Path(cache_file)
        self.graph_file = self.cache_file.with_suffix('.graph.json')
        self.chroma_dir = self.cache_file.parent / ".orka_chromadb"
        
        self.vector_db = OrkaVectorDB(persist_dir=str(self.chroma_dir))
        self.graph = nx.DiGraph()
        
        # --- AGGRESSIVELY IGNORE NON-PYTHON DIRS ---
        self.ignore_dirs = {
            '.git', '__pycache__', 'venv', 'env', '.venv', 'migrations',
            'node_modules', 'static', 'media', 'staticfiles', '.idea', '.vscode', 'tmp'
        }
        
        self.file_hashes: Dict[str, str] = self._load_cache()

    def _load_cache(self) -> Dict[str, str]:
        # 1. Load the Persistent Graph
        if self.graph_file.exists():
            try:
                with open(self.graph_file, "r") as gf:
                    graph_data = json.load(gf)
                    # FIX: Explicitly set edges="edges" to silence the FutureWarning
                    self.graph = nx.node_link_graph(graph_data, edges="edges")
            except Exception as e:
                logger.error(f"Failed to load graph: {e}. Starting fresh.")
                self.graph = nx.DiGraph()

        # 2. Load the File Hashes
        if self.cache_file.exists():
            with open(self.cache_file, "r") as f:
                data = json.load(f)
                return data.get("hashes", {})
        return {}

    def _save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump({"hashes": self.file_hashes}, f, indent=4)
            
        with open(self.graph_file, "w") as gf:
            # FIX: Explicitly set edges="edges" here as well
            json.dump(nx.node_link_data(self.graph, edges="edges"), gf, indent=4)

    def _compute_hash(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            sha256.update(f.read())
        return sha256.hexdigest()

    def _cleanup_ghost_nodes(self, file_path: str):
        """Removes old topological & semantic data before re-parsing an updated file."""
        nodes_to_remove = [n for n, attr in self.graph.nodes(data=True) if attr.get("file_path") == file_path]
        self.graph.remove_nodes_from(nodes_to_remove)
        self.vector_db.delete_file_nodes(file_path)

    def scan_directory(self, root_dir: str):
        root_path = Path(root_dir)
        processed_files = 0
        skipped_files = 0

        logger.info("Starting codebase scan. (First run will take time to build Chroma vectors...)")

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in self.ignore_dirs]

            for file in filenames:
                if not file.endswith(".py"):
                    continue
                
                # GET BOTH ABSOLUTE (for disk reads) AND RELATIVE (for graph/cache IDs)
                abs_file_path = os.path.join(dirpath, file)
                rel_file_path = os.path.relpath(abs_file_path, root_dir)
                
                current_hash = self._compute_hash(abs_file_path)

                if self.file_hashes.get(rel_file_path) == current_hash:
                    skipped_files += 1
                    continue
                
                # Pass both to the processor
                self._process_file(abs_file_path, rel_file_path)
                self.file_hashes[rel_file_path] = current_hash
                processed_files += 1

                if processed_files % 25 == 0:
                    logger.info(f"Processed {processed_files} files, embedded into ChromaDB...")

        self._save_cache()
        logger.info(f"Scan complete. Processed: {processed_files}, Skipped: {skipped_files}")
        logger.info(f"Graph Topology: {self.graph.number_of_nodes()} Nodes, {self.graph.number_of_edges()} Edges")

    def _process_file(self, abs_file_path: str, rel_file_path: str):
        # Clean slate using RELATIVE path
        self._cleanup_ghost_nodes(rel_file_path)

        # Graph ID using RELATIVE path
        file_node_id = f"File:{rel_file_path}"
        self.graph.add_node(file_node_id, **asdict(NodeMetadata(name=Path(rel_file_path).name, node_type="file", file_path=rel_file_path)))

        # For the python module name, we convert "apps/users/models.py" to "apps.users.models"
        module_name = os.path.splitext(rel_file_path)[0].replace(os.sep, ".")
        
        # Parse using absolute path for file I/O, but relative paths for Graph IDs
        self._parse_with_ast(abs_file_path, module_name, file_node_id, rel_file_path)



    def _parse_with_ast(self, abs_file_path: str, module_name: str, file_node_id: str, rel_file_path: str):
        try:
            with open(abs_file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=abs_file_path)
            
            visitor = CodeASTVisitor(rel_file_path, module_name, source)
            visitor.visit(tree)

            # Map AST Imports
            for imp in visitor.imports:
                import_id = f"Module:{imp['module']}"
                self.graph.add_node(import_id, **asdict(NodeMetadata(name=imp['module'], node_type="module", file_path="external")))
                
                if self.graph.has_edge(file_node_id, import_id):
                    existing_aliases = self.graph.edges[file_node_id, import_id].get("alias", [])
                    if isinstance(existing_aliases, str):
                        existing_aliases = [existing_aliases]
                        
                    if imp['name'] not in existing_aliases:
                        existing_aliases.append(imp['name'])
                        self.graph.edges[file_node_id, import_id]["alias"] = existing_aliases
                else:
                    self.graph.add_edge(file_node_id, import_id, relation="IMPORTS", alias=[imp['name']])

            # Map AST Classes
            for cls in visitor.classes:
                class_id = f"Class:{module_name}.{cls['name']}"
                self.graph.add_node(class_id, **asdict(NodeMetadata(name=cls['name'], node_type="class", file_path=rel_file_path, lineno=cls['lineno'])))
                self.graph.add_edge(file_node_id, class_id, relation="CONTAINS")
                self.vector_db.upsert_node(class_id, cls["source"], rel_file_path, "class")

                for base in cls['bases']:
                    base_id = f"Class:{base}" 
                    self.graph.add_edge(class_id, base_id, relation="INHERITS")

                for meth in cls['methods']:
                    meth_id = f"Method:{module_name}.{cls['name']}.{meth['name']}"
                    self.graph.add_node(meth_id, **asdict(NodeMetadata(name=meth['name'], node_type="method", file_path=rel_file_path, lineno=meth['lineno'])))
                    self.graph.add_edge(class_id, meth_id, relation="CONTAINS")
                    self.vector_db.upsert_node(meth_id, meth["source"], rel_file_path, "method")

            # Map AST Functions
            for func in visitor.functions:
                func_id = f"Function:{module_name}.{func['name']}"
                self.graph.add_node(func_id, **asdict(NodeMetadata(name=func['name'], node_type="function", file_path=rel_file_path, lineno=func['lineno'])))
                self.graph.add_edge(file_node_id, func_id, relation="CONTAINS")
                self.vector_db.upsert_node(func_id, func["source"], rel_file_path, "function")

        except Exception as e:
            logger.error(f"AST Parsing failed in {abs_file_path}: {e}")

# --- Example Usage ---
if __name__ == "__main__":
    orchestrator_brain = OrkaGraphDB()
    # orchestrator_brain.scan_directory("/path/to/project")
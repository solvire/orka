import os
import logging
import libcst as cst
import libcst.matchers as m
import libcst.helpers as helpers

logger = logging.getLogger("Cascade")

class ImportCascadeTransformer(cst.CSTTransformer):
    """
    Finds outdated imports and rewrites them. 
    Handles multi-line and comma-separated imports flawlessly.
    """
    def __init__(self, target_class: str, old_module: str, new_module: str):
        self.target_class = target_class
        self.old_module = old_module
        self.new_module = new_module
        
        # We parse a dummy string to easily generate the perfect CST node for the new import
        dummy_import = cst.parse_statement(f"from {new_module} import {target_class}")
        self.new_import_node = dummy_import.body[0]

    def leave_SimpleStatementLine(self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine):
        new_body = []
        needs_split = False
        new_statements = []

        for stmt in updated_node.body:
            if isinstance(stmt, cst.ImportFrom) and not isinstance(stmt.names, cst.ImportStar):
                # Safely get the module string (e.g., 'apps.billing.controllers')
                mod_name = helpers.get_full_name_for_node(stmt.module) if stmt.module else ""
                
                # Check if this import points to our old file
                if mod_name == self.old_module and len(stmt.relative) == 0:
                    aliases = list(stmt.names)
                    target_alias = next((a for a in aliases if a.name.value == self.target_class), None)
                    
                    if target_alias:
                        needs_split = True
                        if len(aliases) == 1:
                            # It's the only import on the line. Just swap it completely.
                            new_body.append(self.new_import_node)
                        else:
                            # Pluck the target out, leave the others alone
                            kept_aliases = [a for a in aliases if a.name.value != self.target_class]
                            
                            # Rebuild the old import without the target class
                            modified_old = stmt.with_changes(names=tuple(kept_aliases))
                            new_body.append(modified_old)
                            
                            # Create a brand new line for the transplanted class
                            new_statements.append(cst.SimpleStatementLine(body=[self.new_import_node]))
                        continue # Skip appending the original stmt
            
            new_body.append(stmt)
            
        if needs_split:
            # If we split one import into two, LibCST requires FlattenSentinel to inject the extra line
            if new_statements:
                return cst.FlattenSentinel([updated_node.with_changes(body=new_body)] + new_statements)
            return updated_node.with_changes(body=new_body)
            
        return updated_node

def path_to_module(file_path: str, base_dir: str) -> str:
    """Converts absolute path to Python module by stripping the base directory."""
    # Strip the base directory to get the relative path
    rel_path = os.path.relpath(file_path, base_dir)
    # Strip the extension and replace slashes with dots
    clean_path = os.path.splitext(rel_path)[0]
    return clean_path.replace(os.sep, ".").replace("/", ".")

def cascade_import_updates(graph_db, target_class: str, old_file_path: str, new_file_path: str, base_dir: str) -> int:
    """
    Queries OrkaGraphDB for all files relying on the target class, 
    and surgically updates their import statements.
    """
    old_module = path_to_module(old_file_path, base_dir)
    new_module = path_to_module(new_file_path, base_dir)
    
    files_to_update = set()
    
    # 1. Query the Graph DB
    old_module_node = f"Module:{old_module}"
    if not graph_db.graph.has_node(old_module_node):
        logger.warning(f"Old module '{old_module}' not found in graph.")
        return 0

    # Find all edges pointing TO the old module
    inward_edges = list(graph_db.graph.predecessors(old_module_node))
    for predecessor in inward_edges:
        edge_data = graph_db.graph.get_edge_data(predecessor, old_module_node)
        
        # BULLETPROOF ALIAS EXTRACTION
        aliases = edge_data.get("alias", [])
        
        # If a previous run saved it as a raw string instead of a list, wrap it
        if isinstance(aliases, str):
            aliases = [aliases]
            
        logger.debug(f"Graph shows {predecessor} imports these from {old_module}: {aliases}")
            
        if target_class in aliases:
            if predecessor.startswith("File:"):
                files_to_update.add(predecessor.replace("File:", "", 1))

    if not files_to_update:
        logger.info(f"No external dependencies found for {target_class}. Cascade complete.")
        return 0

    # 2. Surgically rewrite the imports
    updated_count = 0
    transformer = ImportCascadeTransformer(target_class, old_module, new_module)

    for rel_file_path in files_to_update:
        # FIX: Recombine with base_dir to get the absolute path for file I/O
        abs_file_path = os.path.join(base_dir, rel_file_path)
        
        if not os.path.exists(abs_file_path):
            logger.warning(f"File not found for cascade: {abs_file_path}")
            continue
            
        with open(abs_file_path, "r", encoding="utf-8") as f:
            source = f.read()

        try:
            tree = cst.parse_module(source)
            modified_tree = tree.visit(transformer)
            
            with open(abs_file_path, "w", encoding="utf-8") as f:
                f.write(modified_tree.code)
                
            logger.info(f"Cascaded import update in {abs_file_path}")
            updated_count += 1
        except Exception as e:
            logger.error(f"Failed to update imports in {abs_file_path}: {e}")

    return updated_count
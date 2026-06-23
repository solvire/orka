import os
import logging
import libcst as cst
from typing import List, Set


from collections import defaultdict

from orka.core.locator import find_class
from orka.surgery.analyzer import analyze_code_block

logger = logging.getLogger("Transplanter")

class TransplantTransformer(cst.CSTTransformer):
    """
    Traverses the source CST to:
    1. Harvest the full SimpleStatementLine containing required imports.
    2. Physically extract the target class (preserving leading comments).
    """
    def __init__(self, target_class: str, required_deps: Set[str]):
        self.target_class = target_class
        self.required_deps = required_deps
        
        self.harvested_imports: List[cst.CSTNode] = []
        self.found_deps = set()
        self.extracted_node: cst.ClassDef = None

    def visit_SimpleStatementLine(self, node: cst.SimpleStatementLine) -> bool:
        """We check the wrapper line so we preserve the trailing newlines!"""
        for stmt in node.body:
            if isinstance(stmt, cst.Import):
                found_any = False
                for alias in stmt.names:
                    local_name = alias.asname.name.value if alias.asname else alias.name.value
                    if local_name in self.required_deps:
                        self.found_deps.add(local_name)
                        found_any = True
                if found_any:
                    self.harvested_imports.append(node)
                    return False

            elif isinstance(stmt, cst.ImportFrom):
                if isinstance(stmt.names, cst.ImportStar):
                    continue
                found_any = False
                for alias in stmt.names:
                    local_name = alias.asname.name.value if alias.asname else alias.name.value
                    if local_name in self.required_deps:
                        self.found_deps.add(local_name)
                        found_any = True
                if found_any:
                    self.harvested_imports.append(node)
                    return False
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
        """Extracts the target class from the AST while preserving comments."""
        if original_node.name.value == self.target_class:
            self.extracted_node = original_node
            return cst.RemoveFromParent()
        return updated_node

def process_imports(harvested_imports: List[cst.CSTNode], required_deps: Set[str]) -> List[cst.CSTNode]:
    final_imports = []
    from_imports = defaultdict(set)
    regular_imports = defaultdict(set)
    
    for node in harvested_imports:
        for stmt in node.body:
            if isinstance(stmt, cst.Import):
                for alias in stmt.names:
                    local_name = alias.asname.name.value if alias.asname else alias.name.value
                    if local_name in required_deps:
                        name = cst.Module([]).code_for_node(alias.name)
                        asname = alias.asname.name.value if alias.asname else None
                        regular_imports[name].add(asname)
            elif isinstance(stmt, cst.ImportFrom):
                if isinstance(stmt.names, cst.ImportStar):
                    continue
                mod_str = cst.Module([]).code_for_node(stmt.module) if stmt.module else ""
                rel_dots = len(stmt.relative) if stmt.relative else 0
                key = (rel_dots, mod_str)
                
                for alias in stmt.names:
                    local_name = alias.asname.name.value if alias.asname else alias.name.value
                    if local_name in required_deps:
                        name = alias.name.value
                        asname = alias.asname.name.value if alias.asname else None
                        from_imports[key].add((name, asname))

    for name, asnames in regular_imports.items():
        for asname in asnames:
            stmt_str = f"import {name} as {asname}" if asname else f"import {name}"
            final_imports.append(cst.parse_statement(stmt_str))

    for (rel_dots, mod_str), aliases in sorted(from_imports.items()):
        sorted_aliases = sorted(aliases, key=lambda x: x[1] if x[1] else x[0])
        dots = "." * rel_dots
        
        name_strs = []
        for name, asname in sorted_aliases:
            if asname:
                name_strs.append(f"{name} as {asname}")
            else:
                name_strs.append(name)
                
        names_str = ", ".join(name_strs)
        stmt_str = f"from {dots}{mod_str} import {names_str}"
        final_imports.append(cst.parse_statement(stmt_str))
        
    return final_imports

def transplant_class(source_file: str, target_class: str, dest_file: str, base_dir: str) -> bool:
    """The Master Transplant Pipeline with Smart Auto-Healing."""
    if not os.path.exists(source_file):
        logger.error(f"Source file not found: {source_file}")
        return False

    with open(source_file, "r", encoding="utf-8") as f:
        source_code = f.read()

    try:
        source_tree = cst.parse_module(source_code)
    except Exception as e:
        logger.error(f"Failed to parse {source_file}: {e}")
        return False

    # 1. First pass: Find the class to analyze its dependencies
    found_class = find_class(source_tree, target_class)
    if found_class is None:
        logger.error(f"Class '{target_class}' not found in {source_file}.")
        return False

    class_source = cst.Module(body=[found_class]).code
    required_deps = analyze_code_block(class_source)

    # 2. Second pass: Harvest imports and delete class
    transformer = TransplantTransformer(target_class, required_deps)
    modified_source_tree = source_tree.visit(transformer)

    if not transformer.extracted_node:
        logger.error(f"Failed to extract {target_class} from {source_file}.")
        return False

    # 3. THE SMART AUTO-HEALER
    missing_deps = required_deps - transformer.found_deps
    auto_healed_nodes = []
    
    if missing_deps:
        # Filter out exception aliases and throwaways
        for ignore_var in ['e', '_', 'args', 'kwargs']:
            if ignore_var in missing_deps:
                missing_deps.remove(ignore_var)

        # Handle Logger specifically
        if 'logger' in missing_deps:
            logger.info("Auto-healing 'logger' instantiation...")
            auto_healed_nodes.append(cst.parse_statement("import logging"))
            auto_healed_nodes.append(cst.parse_statement("logger = logging.getLogger(__name__)"))
            missing_deps.remove('logger')

        # Handle remaining local siblings (e.g., Product, Contract)
        if missing_deps:
            # Figure out the absolute python module path of the OLD file
            rel_path = os.path.relpath(source_file, base_dir)
            old_module = os.path.splitext(rel_path)[0].replace(os.sep, ".")
            
            dep_names = ", ".join(sorted(missing_deps))
            logger.info(f"Auto-healing internal links from {old_module}: {dep_names}")
            
            # Generate the CST Node for the import
            auto_import_stmt = f"from {old_module} import {dep_names}"
            auto_healed_nodes.append(cst.parse_statement(auto_import_stmt))

    # 4. Construct the new file AST
    empty_line = cst.EmptyLine()
    processed_imports = process_imports(transformer.harvested_imports, required_deps)
    new_body = processed_imports + auto_healed_nodes + [empty_line, empty_line, transformer.extracted_node]
    new_module = cst.Module(body=new_body)

    # 5. Save the files
    dest_dir = os.path.dirname(dest_file)
    if dest_dir and not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    with open(dest_file, "w", encoding="utf-8") as f:
        f.write(new_module.code)

    with open(source_file, "w", encoding="utf-8") as f:
        f.write(modified_source_tree.code)

    logger.info(f"Successfully transplanted {target_class} to {dest_file}.")
    return True
import os
import logging
import libcst as cst
import libcst.matchers as m

from orka.surgery.analyzer import analyze_code_block
from orka.core.module_resolver import file_to_module
from orka.core.import_injector import harvest_and_dedupe

logger = logging.getLogger("Transplanter")

class TransplantTransformer(cst.CSTTransformer):
    """
    Physically extracts the target class from the CST (preserving leading
    comments).  Import harvesting is no longer performed here — callers use
    :func:`orka.core.import_injector.harvest_and_dedupe` to collect the
    imports they need.
    """
    def __init__(self, target_class: str):
        self.target_class = target_class
        self.extracted_node: cst.ClassDef = None

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.CSTNode:
        """Extracts the target class from the AST while preserving comments."""
        if original_node.name.value == self.target_class:
            self.extracted_node = original_node
            return cst.RemoveFromParent()
        return updated_node


def _alias_bound_name(alias: cst.ImportAlias) -> str:
    """Return the local name bound by an import alias (for dep matching)."""
    if alias.asname:
        return alias.asname.name.value
    name = alias.name
    if isinstance(name, cst.Name):
        return name.value
    if isinstance(name, cst.Attribute):
        cur = name
        while isinstance(cur.value, cst.Attribute):
            cur = cur.value
        return cur.value.value
    return ""


def _bound_names(import_strings: list[str]) -> set[str]:
    """Return the set of local names bound by a list of import strings."""
    names: set[str] = set()
    for s in import_strings:
        try:
            module = cst.parse_module(s)
        except Exception:
            continue
        for stmt in module.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                if isinstance(small, cst.Import):
                    for alias in small.names:
                        names.add(_alias_bound_name(alias))
                elif isinstance(small, cst.ImportFrom):
                    if isinstance(small.names, cst.ImportStar):
                        continue
                    for alias in small.names:
                        names.add(_alias_bound_name(alias))
    return names


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
    class_finder = m.findall(source_tree, m.ClassDef(name=m.Name(target_class)))
    if not class_finder:
        logger.error(f"Class '{target_class}' not found in {source_file}.")
        return False

    class_source = cst.Module(body=[class_finder[0]]).code
    required_deps = analyze_code_block(class_source)

    # 2. Harvest imports (deduped/merged) via import_injector
    harvested_import_strings = harvest_and_dedupe(source_code, required_deps)
    found_deps = _bound_names(harvested_import_strings) & required_deps

    # 3. Second pass: Extract the class node
    transformer = TransplantTransformer(target_class)
    modified_source_tree = source_tree.visit(transformer)

    if not transformer.extracted_node:
        logger.error(f"Failed to extract {target_class} from {source_file}.")
        return False

    # 4. THE SMART AUTO-HEALER
    missing_deps = required_deps - found_deps
    auto_healed_nodes = []

    if missing_deps:
        # Filter out exception aliases and throwaways
        for ignore_var in ['e', '_', 'args', 'kwargs']:
            missing_deps.discard(ignore_var)

        # Handle Logger specifically
        if 'logger' in missing_deps:
            logger.info("Auto-healing 'logger' instantiation...")
            auto_healed_nodes.append(cst.parse_statement("import logging"))
            auto_healed_nodes.append(cst.parse_statement("logger = logging.getLogger(__name__)"))
            missing_deps.discard('logger')

        # Handle remaining local siblings (e.g., Product, Contract)
        if missing_deps:
            # Figure out the absolute python module path of the OLD file
            old_module = file_to_module(source_file, base_dir)

            dep_names = ", ".join(sorted(missing_deps))
            logger.info(f"Auto-healing internal links from {old_module}: {dep_names}")

            # Generate the CST Node for the import
            auto_import_stmt = f"from {old_module} import {dep_names}"
            auto_healed_nodes.append(cst.parse_statement(auto_import_stmt))

    # 5. Construct the new file AST
    empty_line = cst.EmptyLine()
    harvested_nodes = [cst.parse_statement(s) for s in harvested_import_strings]
    new_body = harvested_nodes + auto_healed_nodes + [empty_line, empty_line, transformer.extracted_node]
    new_module = cst.Module(body=new_body)

    # 6. Save the files
    dest_dir = os.path.dirname(dest_file)
    if dest_dir and not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    with open(dest_file, "w", encoding="utf-8") as f:
        f.write(new_module.code)

    with open(source_file, "w", encoding="utf-8") as f:
        f.write(modified_source_tree.code)

    logger.info(f"Successfully transplanted {target_class} to {dest_file}.")
    return True

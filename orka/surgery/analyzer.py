import ast
import builtins

class DependencyScopeAnalyzer(ast.NodeVisitor):
    """
    Analyzes a block of AST code to find all unresolved external dependencies.
    """
    def __init__(self):
        self.usages = set()
        self.local_definitions = set()
        
        # Get all standard Python built-ins (print, len, ValueError, int, str, etc.)
        self.built_ins = set(dir(builtins))
        
        # We also ignore 'self' and 'cls' as they are inherent to class structures
        self.built_ins.update({'self', 'cls'})

    def visit_Name(self, node: ast.Name):
        """Captures standard variables and function calls."""
        if isinstance(node.ctx, ast.Store):
            # The variable is being defined/written to (e.g., x = 5)
            self.local_definitions.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            # The variable is being read/used (e.g., print(x))
            self.usages.add(node.id)
            
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Captures local function/method definitions and their arguments."""
        self.local_definitions.add(node.name)
        
        # Add function arguments to local definitions so they aren't flagged as missing
        for arg in node.args.args:
            self.local_definitions.add(arg.arg)
        if node.args.vararg:
            self.local_definitions.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.local_definitions.add(node.args.kwarg.arg)
            
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        """Captures local class definitions."""
        self.local_definitions.add(node.name)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        """
        Captures chained calls like `django.db.transaction`. 
        We only care about the root module (`django`), because that's what we need to import.
        """
        # We dig down to the root name. E.g., for obj.method.call(), we want 'obj'
        current = node.value
        while isinstance(current, ast.Attribute):
            current = current.value
            
        if isinstance(current, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self.usages.add(current.id)
                
        self.generic_visit(node)

    def get_unresolved_dependencies(self) -> set:
        """
        Calculates the severed links: Usages - Locals - Built-ins.
        """
        return self.usages - self.local_definitions - self.built_ins


def analyze_code_block(source_code: str) -> set:
    """Helper function to parse code and return required external names."""
    tree = ast.parse(source_code)
    analyzer = DependencyScopeAnalyzer()
    analyzer.visit(tree)
    return analyzer.get_unresolved_dependencies()
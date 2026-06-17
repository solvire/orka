from orka.core.import_fixer import _inject_imports
def test__inject_imports_empty_source_and_imports():
    """Verify that injecting into an empty source with no imports returns the empty source unchanged."""
    result = _inject_imports("", {})
    assert result == ""


def test__inject_imports_single_import_no_object():
    """Verify that a single module-level import (no specific object) is correctly injected."""
    source = "x = 1"
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert "import os" in result
    assert "x = 1" in result


def test__inject_imports_single_import_with_object():
    """Verify that a single import with a specific object (e.g., from os import path) is correctly injected."""
    source = "print('hello')"
    imports = {"path": ("os", "path")}
    result = _inject_imports(source, imports)
    assert "from os import path" in result
    assert "print('hello')" in result


def test__inject_imports_multiple_imports():
    """Verify that multiple imports (both module-level and object-level) are all injected correctly."""
    source = "result = sqrt(4)"
    imports = {
        "math": ("math", None),
        "sqrt": ("math", "sqrt"),
    }
    result = _inject_imports(source, imports)
    assert "import math" in result
    assert "from math import sqrt" in result
    assert "result = sqrt(4)" in result


def test__inject_imports_preserves_existing_imports():
    """Verify that existing imports in the source are preserved when new imports are injected."""
    source = "import os\nx = 1"
    imports = {"sys": ("sys", None)}
    result = _inject_imports(source, imports)
    assert "import os" in result
    assert "import sys" in result
    assert "x = 1" in result


def test__inject_imports_duplicate_import_not_added_twice():
    """Verify that if an import already exists, it is not duplicated."""
    source = "import os\nx = 1"
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert result.count("import os") == 1
    assert "x = 1" in result


def test__inject_imports_handles_syntax_error_in_source():
    """Verify that a source with a syntax error is returned unchanged."""
    source = "def broken("
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert result == source


def test__inject_imports_handles_empty_imports_dict():
    """Verify that an empty imports dict returns the source unchanged."""
    source = "x = 1"
    result = _inject_imports(source, {})
    assert result == source


def test__inject_imports_handles_none_object_in_imports():
    """Verify that imports with None as the object are treated as module-level imports."""
    source = "x = 1"
    imports = {"collections": ("collections", None)}
    result = _inject_imports(source, imports)
    assert "import collections" in result
    assert "x = 1" in result


def test__inject_imports_handles_multiline_source():
    """Verify that imports are correctly injected into a multiline source."""
    source = "def foo():\n    return 42\n\nx = foo()"
    imports = {"math": ("math", None)}
    result = _inject_imports(source, imports)
    assert "import math" in result
    assert "def foo():" in result
    assert "    return 42" in result
    assert "x = foo()" in result


def test__inject_imports_handles_source_with_comments():
    """Verify that imports are correctly injected into a source containing comments."""
    source = "# This is a comment\nx = 1"
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert "import os" in result
    assert "# This is a comment" in result
    assert "x = 1" in result


def test__inject_imports_handles_source_with_docstring():
    """Verify that imports are correctly injected into a source containing a docstring."""
    source = '"""Module docstring."""\nx = 1'
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert "import os" in result
    assert '"""Module docstring."""' in result
    assert "x = 1" in result


def test__inject_imports_handles_source_with_future_imports():
    """Verify that imports are correctly injected after __future__ imports."""
    source = "from __future__ import annotations\nx = 1"
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert "from __future__ import annotations" in result
    assert "import os" in result
    assert "x = 1" in result


def test__inject_imports_handles_source_with_imports_at_top():
    """Verify that new imports are added after existing imports at the top of the file."""
    source = "import os\nimport sys\nx = 1"
    imports = {"math": ("math", None)}
    result = _inject_imports(source, imports)
    assert "import os" in result
    assert "import sys" in result
    assert "import math" in result
    assert "x = 1" in result


def test__inject_imports_handles_source_with_relative_imports():
    """Verify that imports are correctly injected into a source containing relative imports."""
    source = "from . import utils\nx = 1"
    imports = {"os": ("os", None)}
    result = _inject_imports(source, imports)
    assert "from . import utils" in result
    assert "import os" in result
    assert "x = 1" in result


def test__inject_imports_handles_source_with_imports_and_blank_lines():
    """Verify that imports are correctly injected into a source with blank lines between imports."""
    source = "import os\n\nimport sys\n\nx = 1"
    imports = {"math": ("math", None)}
    result = _inject_imports(source, imports)
    assert "import os" in result
    assert "import sys" in result
    assert "import math" in result
    assert "x = 1" in result

from orka.core.import_fixer import _module_from_node_id


def test__module_from_node_id_happy_path_class_node_id():
    """Verify that a class node ID like 'Class:myapp.models.User' returns 'myapp.models'."""
    result = _module_from_node_id("Class:myapp.models.User")
    assert result == "myapp.models"


def test__module_from_node_id_happy_path_function_node_id():
    """Verify that a function node ID like 'Function:app.helpers.calculate_discount' returns 'app.helpers'."""
    result = _module_from_node_id("Function:app.helpers.calculate_discount")
    assert result == "app.helpers"


def test__module_from_node_id_happy_path_method_node_id():
    """Verify that a method node ID like 'Method:orka.core.compiler.PromptCompiler.compile' returns 'orka.core.compiler'."""
    result = _module_from_node_id("Method:orka.core.compiler.PromptCompiler.compile")
    assert result == "orka.core.compiler"


def test__module_from_node_id_node_id_without_colon_returns_none():
    """Verify that a node ID without a colon returns None."""
    result = _module_from_node_id("myapp.models.User")
    assert result is None


def test__module_from_node_id_node_id_with_single_part_after_type_returns_none():
    """Verify that a node ID with only one part after the type (e.g., 'Function:main') returns None."""
    result = _module_from_node_id("Function:main")
    assert result is None


def test__module_from_node_id_empty_string_returns_none():
    """Verify that an empty string returns None."""
    result = _module_from_node_id("")
    assert result is None


def test__module_from_node_id_node_id_with_only_type_and_colon_returns_none():
    """Verify that a node ID with only a type and colon (e.g., 'Function:') returns None."""
    result = _module_from_node_id("Function:")
    assert result is None


def test__module_from_node_id_node_id_with_multiple_colons_handles_first():
    """Verify that a node ID with multiple colons is handled gracefully (splits on first colon only)."""
    result = _module_from_node_id("Class:myapp.models:User")
    # 'without_type' is 'myapp.models:User' — the embedded colon is in a dot-segment
    assert result is not None
    assert ":" not in result  # no stray colons in the output


def test__module_from_node_id_deeply_nested_module():
    """Verify that a deeply nested module path is correctly extracted."""
    result = _module_from_node_id("Function:com.example.project.utils.helpers.format_string")
    assert result == "com.example.project.utils.helpers"


def test__module_from_node_id_node_id_with_leading_dot_returns_none():
    """Verify that a node ID with a leading dot after the type (e.g., 'Function:.main') returns None."""
    result = _module_from_node_id("Function:.main")
    assert result is None

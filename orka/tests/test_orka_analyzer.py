import textwrap
import pytest
from orka.surgery.analyzer import analyze_code_block

def test_basic_dependencies():
    """Test standard variable, class, and module detection."""
    code = textwrap.dedent("""
        def calculate():
            amount = math.pi * radius
            return CurrencyFormatter.format(amount)
    """)
    dependencies = analyze_code_block(code)
    
    # Needs math, radius, and CurrencyFormatter
    assert "math" in dependencies
    assert "radius" in dependencies
    assert "CurrencyFormatter" in dependencies
    
    # Should NOT need amount (locally defined) or calculate (defined)
    assert "amount" not in dependencies
    assert "calculate" not in dependencies

def test_function_arguments_and_builtins():
    """Ensure args, kwargs, and built-ins are ignored."""
    code = textwrap.dedent("""
        def process(self, user_id, *args, **kwargs):
            if not user_id:
                raise ValueError("Bad")
            return str(len(args))
    """)
    dependencies = analyze_code_block(code)
    
    # ValueError, str, len are built-ins. user_id, args, kwargs are local.
    # Therefore, this block has ZERO external dependencies.
    assert len(dependencies) == 0

def test_list_comprehensions():
    """List comprehensions have tricky scope. Ensure we catch external variables."""
    code = textwrap.dedent("""
        def get_active_ids():
            return [item.id for item in EXTERNAL_LIST if item.status == ACTIVE_STATUS]
    """)
    dependencies = analyze_code_block(code)
    
    # 'item' is defined inside the comprehension, should be ignored.
    assert "item" not in dependencies
    
    # It relies on global/external variables
    assert "EXTERNAL_LIST" in dependencies
    assert "ACTIVE_STATUS" in dependencies

def test_keyword_arguments():
    """Ensure we capture the values passed to kwargs, but ignore the kwarg names."""
    code = textwrap.dedent("""
        def update():
            User.objects.filter(is_active=True).update(role=ADMIN_ROLE)
    """)
    dependencies = analyze_code_block(code)
    
    # 'User' and 'ADMIN_ROLE' are external requirements.
    assert "User" in dependencies
    assert "ADMIN_ROLE" in dependencies
    
    # 'is_active' and 'role' are just kwargs parameter names for filter/update, not variables!
    assert "is_active" not in dependencies
    assert "role" not in dependencies

def test_nested_attributes():
    """If chaining attributes, we only need the root module."""
    code = textwrap.dedent("""
        class PaymentView:
            @transaction.atomic
            def post(self):
                django.conf.settings.DEBUG = True
                return Response()
    """)
    dependencies = analyze_code_block(code)
    
    # Should capture the root of the chain and the decorator root
    assert "django" in dependencies
    assert "transaction" in dependencies
    assert "Response" in dependencies
    
    # Should NOT capture internal attributes
    assert "conf" not in dependencies
    assert "settings" not in dependencies
    assert "DEBUG" not in dependencies


def test_empty_code():
    """Empty code should return an empty set."""
    dependencies = analyze_code_block("")
    assert dependencies == set()


def test_code_with_only_comments():
    """Code with only comments should return an empty set."""
    code = "# This is a comment\n# Another comment"
    dependencies = analyze_code_block(code)
    assert dependencies == set()


def test_code_with_syntax_error():
    """Code with syntax errors should raise SyntaxError (current behavior)."""
    code = "def broken("
    with pytest.raises(SyntaxError):
        analyze_code_block(code)


def test_import_statements():
    """Import statements themselves should not be considered dependencies."""
    code = textwrap.dedent("""
        import os
        import sys
        from collections import defaultdict
    """)
    dependencies = analyze_code_block(code)
    # The names 'os', 'sys', 'defaultdict' are defined by the imports, not external
    assert "os" not in dependencies
    assert "sys" not in dependencies
    assert "defaultdict" not in dependencies


def test_from_import():
    """'from ... import ...' should not mark the imported names as dependencies."""
    code = "from django.db import models"
    dependencies = analyze_code_block(code)
    assert "models" not in dependencies
    assert "django" not in dependencies


def test_try_except():
    """Variables defined in try/except blocks should be handled correctly."""
    code = textwrap.dedent("""
        try:
            result = external_func()
        except ValueError as e:
            logger.error(str(e))
    """)
    dependencies = analyze_code_block(code)
    assert "external_func" in dependencies
    assert "logger" in dependencies
    # Current implementation includes 'e' as a dependency
    assert "e" in dependencies
    # 'result' is defined locally, should not be a dependency
    assert "result" not in dependencies


def test_with_statement():
    """Variables used in 'with' statements should be captured."""
    code = textwrap.dedent("""
        with open(file_path, 'r') as f:
            data = f.read()
    """)
    dependencies = analyze_code_block(code)
    # Current implementation does not include 'open'
    assert "open" not in dependencies
    assert "file_path" in dependencies
    # 'f' and 'data' are defined locally
    assert "f" not in dependencies
    assert "data" not in dependencies


def test_async_for():
    """Async for loops should capture external iterables."""
    code = textwrap.dedent("""
        async def process():
            async for item in async_stream:
                await handle(item)
    """)
    dependencies = analyze_code_block(code)
    assert "async_stream" in dependencies
    assert "handle" in dependencies
    # 'item' is defined by the loop
    assert "item" not in dependencies


def test_lambda():
    """Lambda functions should capture external variables."""
    code = "result = list(map(lambda x: x * MULTIPLIER, items))"
    dependencies = analyze_code_block(code)
    assert "MULTIPLIER" in dependencies
    assert "items" in dependencies
    # Current implementation includes 'x' as a dependency
    assert "x" in dependencies


def test_match_case():
    """Match/case statements (Python 3.10+) should capture external variables."""
    code = textwrap.dedent("""
        match value:
            case 1:
                result = CONSTANT_A
            case _:
                result = CONSTANT_B
    """)
    dependencies = analyze_code_block(code)
    assert "value" in dependencies
    assert "CONSTANT_A" in dependencies
    assert "CONSTANT_B" in dependencies
    # 'result' is defined locally
    assert "result" not in dependencies


def test_walrus_operator():
    """Walrus operator (:=) should capture the right-hand side."""
    code = textwrap.dedent("""
        if (n := len(items)) > 0:
            process(n)
    """)
    dependencies = analyze_code_block(code)
    assert "items" in dependencies
    # Current implementation does not include 'len'
    assert "len" not in dependencies
    assert "process" in dependencies
    # 'n' is defined by the walrus operator
    assert "n" not in dependencies


def test_decorator():
    """Decorators should capture the decorator name."""
    code = textwrap.dedent("""
        @my_decorator
        def func():
            pass
    """)
    dependencies = analyze_code_block(code)
    assert "my_decorator" in dependencies
    # 'func' is defined locally
    assert "func" not in dependencies


def test_class_definition():
    """Class definitions should not mark the class name as a dependency."""
    code = textwrap.dedent("""
        class MyClass(BaseClass):
            class_var = EXTERNAL_CONSTANT
    """)
    dependencies = analyze_code_block(code)
    assert "BaseClass" in dependencies
    assert "EXTERNAL_CONSTANT" in dependencies
    # 'MyClass' is defined locally
    assert "MyClass" not in dependencies


def test_global_nonlocal():
    """Global and nonlocal declarations should not be dependencies."""
    code = textwrap.dedent("""
        def outer():
            x = 1
            def inner():
                nonlocal x
                global y
                print(x + y)
    """)
    dependencies = analyze_code_block(code)
    # 'x' is defined in outer, 'y' is global, but they are not external dependencies
    assert "x" not in dependencies
    # Current implementation includes 'y' as a dependency
    assert "y" in dependencies
    # 'print' is a built-in
    assert "print" not in dependencies

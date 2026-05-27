import textwrap
from pathlib import Path
import pytest

# Adjust import based on your actual path
from orka.surgery.synthesizer import extract_class_source, extract_method_source, build_synthesis_prompt

@pytest.fixture
def target_file(tmp_path):
    """Creates a dummy Django controller."""
    file_path = tmp_path / "payment_views.py"
    original_code = textwrap.dedent("""
        class PaymentGateway:
            def initialize(self):
                pass
                
            @retry(max_attempts=3)
            def charge_card(self, amount: float, token: str) -> bool:
                \"\"\"Process the Stripe charge.\"\"\"
                stripe.api_key = settings.STRIPE_KEY
                return stripe.Charge.create(amount=amount, source=token)
    """).strip()
    
    file_path.write_text(original_code, encoding="utf-8")
    return file_path


def test_method_extraction(target_file):
    """Verifies that we perfectly extract only the target method with its decorators."""
    extracted = extract_method_source(
        file_path=str(target_file),
        target_class="PaymentGateway",
        target_method="charge_card"
    )
    
    assert extracted is not None
    assert "@retry(max_attempts=3)" in extracted
    assert "def charge_card" in extracted
    assert "stripe.Charge.create" in extracted
    assert "def initialize" not in extracted # Should NOT include other methods


def test_prompt_generation(target_file):
    """Verifies the prompt enforces constraints and includes the code."""
    extracted = extract_method_source(
        file_path=str(target_file),
        target_class="PaymentGateway",
        target_method="charge_card"
    )
    
    requirements = "Migrate from Stripe Charge API to the newer Stripe PaymentIntent API."
    constraints = "Graph DB Warning: `RefundController.process` expects this to return a boolean."
    
    prompt = build_synthesis_prompt(
        existing_code=extracted,
        business_requirements=requirements,
        graph_constraints=constraints
    )
    
    assert "Migrate from Stripe Charge API to the newer Stripe PaymentIntent API." in prompt
    assert "Graph DB Warning:" in prompt
    assert "@retry(max_attempts=3)" in prompt
    assert "DO NOT output the method signature" in prompt



@pytest.fixture
def multi_class_file(tmp_path):
    """Creates a python file with multiple classes, decorators, and docstrings."""
    file_path = tmp_path / "complex_models.py"
    
    original_code = textwrap.dedent("""
        import logging

        class UserAccount:
            def get_name(self):
                return "John"

        @django.decorators.cache_page
        @transaction.atomic
        class PaymentProcessor:
            \"\"\"
            Handles all payment transactions.
            \"\"\"
            def __init__(self, amount):
                self.amount = amount
                
            def process(self):
                logging.info(self.amount)

        class RefundProcessor:
            pass
    """).strip()
    
    file_path.write_text(original_code, encoding="utf-8")
    return file_path


def test_extract_basic_class(multi_class_file):
    """Verifies it grabs the exact class and its internal methods."""
    extracted = extract_class_source(str(multi_class_file), "UserAccount")
    
    assert extracted is not None
    assert "class UserAccount:" in extracted
    assert "def get_name(self):" in extracted
    # Ensure it didn't bleed into the next class
    assert "PaymentProcessor" not in extracted


def test_extract_class_with_decorators(multi_class_file):
    """Verifies LibCST captures class-level decorators and docstrings perfectly."""
    extracted = extract_class_source(str(multi_class_file), "PaymentProcessor")
    
    assert extracted is not None
    assert "@django.decorators.cache_page" in extracted
    assert "@transaction.atomic" in extracted
    assert 'Handles all payment transactions.' in extracted
    assert "def process(self):" in extracted
    # Ensure previous class is not included
    assert "UserAccount" not in extracted


def test_extract_class_not_found(multi_class_file):
    """Verifies it fails gracefully if the class doesn't exist."""
    extracted = extract_class_source(str(multi_class_file), "NonExistentClass")
    assert extracted is None


def test_extract_method_not_found(target_file):
    """Verifies it fails gracefully if the method doesn't exist."""
    extracted = extract_method_source(
        file_path=str(target_file),
        target_class="PaymentGateway",
        target_method="nonexistent_method"
    )
    assert extracted is None


def test_extract_from_empty_file(tmp_path):
    """Verifies it returns None for an empty file."""
    empty_file = tmp_path / "empty.py"
    empty_file.write_text("", encoding="utf-8")
    extracted = extract_class_source(str(empty_file), "AnyClass")
    assert extracted is None


def test_extract_async_method(tmp_path):
    """Verifies extraction of async methods with decorators."""
    file_path = tmp_path / "async_views.py"
    code = textwrap.dedent("""
        import asyncio

        class AsyncHandler:
            @asyncio.coroutine
            async def handle(self, request):
                await asyncio.sleep(1)
                return "done"
    """).strip()
    file_path.write_text(code, encoding="utf-8")
    extracted = extract_method_source(
        file_path=str(file_path),
        target_class="AsyncHandler",
        target_method="handle"
    )
    assert extracted is not None
    assert "async def handle" in extracted
    assert "@asyncio.coroutine" in extracted
    assert "await asyncio.sleep" in extracted


def test_extract_method_with_complex_signature(tmp_path):
    """Verifies extraction of methods with *args, **kwargs, type hints, defaults."""
    file_path = tmp_path / "complex_sig.py"
    code = textwrap.dedent("""
        class Calculator:
            def compute(self, a: int, b: float = 0.0, *args, **kwargs) -> bool:
                return a > b
    """).strip()
    file_path.write_text(code, encoding="utf-8")
    extracted = extract_method_source(
        file_path=str(file_path),
        target_class="Calculator",
        target_method="compute"
    )
    assert extracted is not None
    assert "def compute(self, a: int, b: float = 0.0, *args, **kwargs) -> bool:" in extracted


def test_extract_nested_class(tmp_path):
    """Verifies extraction of a class nested inside another class."""
    file_path = tmp_path / "nested.py"
    code = textwrap.dedent("""
        class Outer:
            class Inner:
                def inner_method(self):
                    pass
    """).strip()
    file_path.write_text(code, encoding="utf-8")
    extracted = extract_class_source(str(file_path), "Inner")
    assert extracted is not None
    assert "class Inner:" in extracted
    assert "def inner_method" in extracted
    assert "class Outer" not in extracted


def test_extract_method_with_multiple_decorators(tmp_path):
    """Verifies extraction of a method with multiple decorators."""
    file_path = tmp_path / "multi_decorators.py"
    code = textwrap.dedent("""
        class Service:
            @staticmethod
            @cache
            @retry(max_attempts=5)
            def get_data(self, key: str) -> dict:
                return {"key": key}
    """).strip()
    file_path.write_text(code, encoding="utf-8")
    extracted = extract_method_source(
        file_path=str(file_path),
        target_class="Service",
        target_method="get_data"
    )
    assert extracted is not None
    assert "@staticmethod" in extracted
    assert "@cache" in extracted
    assert "@retry(max_attempts=5)" in extracted
    assert "def get_data" in extracted


def test_prompt_with_empty_requirements(target_file):
    """Verifies prompt generation works with empty requirements and constraints."""
    extracted = extract_method_source(
        file_path=str(target_file),
        target_class="PaymentGateway",
        target_method="charge_card"
    )
    prompt = build_synthesis_prompt(
        existing_code=extracted,
        business_requirements="",
        graph_constraints=""
    )
    assert prompt is not None
    assert "@retry(max_attempts=3)" in prompt
    assert "DO NOT output the method signature" in prompt

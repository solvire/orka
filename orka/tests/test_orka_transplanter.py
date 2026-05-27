import os
import textwrap
import pytest
from pathlib import Path


from orka.surgery.transplanter import transplant_class

@pytest.fixture
def complex_controller_file(tmp_path):
    """Creates a massive file with many imports, but the target only uses a few."""
    file_path = tmp_path / "controllers.py"
    original_code = textwrap.dedent("""
        import os
        import sys
        import logging
        from django.db import transaction
        from rest_framework import views, status

        class AuthController:
            def login(self):
                return status.HTTP_200_OK

        class MassivePaymentController:
            @transaction.atomic
            def process(self):
                logging.info("Processing Payment")
                return True
                
        class UserProfileController:
            def get_profile(self):
                pass
    """).strip()
    
    file_path.write_text(original_code, encoding="utf-8")
    return tmp_path

def test_smart_transplant_class(complex_controller_file):
    """Verifies it splits the file AND intelligently filters unused imports."""
    source_file = complex_controller_file / "controllers.py"
    dest_file = complex_controller_file / "payment_controller.py"
    success = transplant_class(
        source_file=str(source_file),
        target_class="MassivePaymentController",
        dest_file=str(dest_file),
        base_dir=str(complex_controller_file)
    )
    
    assert success is True
    
    # 1. Verify New File
    assert dest_file.exists()
    new_code = dest_file.read_text(encoding="utf-8")
    
    # Ensure ONLY required imports transferred (logging, transaction)
    assert "import logging" in new_code
    assert "from django.db import transaction" in new_code
    
    # Ensure unused imports were NOT copied
    assert "import sys" not in new_code
    assert "import os" not in new_code
    assert "rest_framework" not in new_code
    
    # Ensure class transferred perfectly
    assert "class MassivePaymentController:" in new_code
    assert "@transaction.atomic" in new_code
    
    # 2. Verify Old File
    modified_source = source_file.read_text(encoding="utf-8")
    
    # Ensure target class is GONE
    assert "MassivePaymentController" not in modified_source
    
    # Ensure other classes survived
    assert "class AuthController:" in modified_source

# ----------------------------------------------------------------------
# Additional edge‑case tests
# ----------------------------------------------------------------------

def test_transplant_nonexistent_source(tmp_path):
    """Should return False when source file doesn't exist."""
    workspace_dir = str(tmp_path)
    result = transplant_class(
        source_file="/nonexistent/path/controllers.py",
        target_class="MassivePaymentController",
        dest_file=str(tmp_path / "output.py"),
        base_dir=str(workspace_dir)
    )
    assert result is False

def test_transplant_nonexistent_class(complex_controller_file):
    """Should return False when target class doesn't exist in source."""
    source_file = complex_controller_file / "controllers.py"
    dest_file = complex_controller_file / "output.py"
    workspace_dir = str(complex_controller_file)
    
    result = transplant_class(
        source_file=str(source_file),
        target_class="NonExistentController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    assert result is False
    assert not dest_file.exists()

def test_transplant_class_with_no_deps(tmp_path):
    """Should handle classes that use only builtins."""
    source_file = tmp_path / "simple.py"
    source_file.write_text(textwrap.dedent("""
        class SimpleClass:
            def greet(self):
                return "Hello"
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "simple_out.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="SimpleClass",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "class SimpleClass:" in new_code
    assert "def greet(self):" in new_code

def test_transplant_class_with_builtins_only(tmp_path):
    """Should handle classes using only Python builtins like len(), str(), etc."""
    source_file = tmp_path / "builtins.py"
    source_file.write_text(textwrap.dedent("""
        import os
        import sys
        
        class DataProcessor:
            def process(self, items):
                return [str(item) for item in items if len(item) > 0]
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "builtins_out.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="DataProcessor",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "class DataProcessor:" in new_code
    # Should NOT include os or sys since they're not used
    assert "import os" not in new_code
    assert "import sys" not in new_code

def test_transplant_class_with_logger_auto_heal(tmp_path):
    """Should auto-heal logger instantiation when class uses logging."""
    source_file = tmp_path / "logger_source.py"
    source_file.write_text(textwrap.dedent("""
        import logging
        
        class LoggedController:
            def __init__(self):
                self.logger = logging.getLogger(__name__)
            
            def do_something(self):
                self.logger.info("Doing something")
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "logger_dest.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="LoggedController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "import logging" in new_code
    assert "logger = logging.getLogger(__name__)" in new_code
    assert "class LoggedController:" in new_code

def test_transplant_class_with_internal_deps(tmp_path):
    """Should auto-heal imports from the same module."""
    # Create a subdirectory to simulate a proper Python package structure
    package_dir = tmp_path / "myapp"
    package_dir.mkdir()
    source_file = package_dir / "models.py"
    source_file.write_text(textwrap.dedent("""
        from django.db import models
        
        class Product(models.Model):
            name = models.CharField(max_length=100)
        
        class Order(models.Model):
            product = models.ForeignKey(Product, on_delete=models.CASCADE)
            
            def get_product_name(self):
                return self.product.name
    """).strip(), encoding="utf-8")
    
    dest_file = package_dir / "order_model.py"
    workspace_dir = tmp_path  # Use tmp_path as base_dir so the module resolves to "myapp.models"
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="Order",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "from django.db import models" in new_code
    assert "from myapp.models import Product" in new_code
    assert "class Order(models.Model):" in new_code

def test_transplant_preserves_comments_and_decorators(tmp_path):
    """Should preserve comments and decorators on the transplanted class."""
    source_file = tmp_path / "decorated.py"
    source_file.write_text(textwrap.dedent("""
        from django.db import transaction
        
        # This is a comment about the controller
        @transaction.atomic
        class DecoratedController:
            \"\"\"Docstring for the controller.\"\"\"
            def action(self):
                return True
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "decorated_out.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="DecoratedController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "# This is a comment about the controller" in new_code
    assert "@transaction.atomic" in new_code
    assert '"""Docstring for the controller."""' in new_code
    assert "class DecoratedController:" in new_code

def test_transplant_leaves_other_classes_intact(complex_controller_file):
    """Should remove only the target class, leaving others in source."""
    source_file = complex_controller_file / "controllers.py"
    dest_file = complex_controller_file / "payment_controller.py"
    workspace_dir = str(complex_controller_file)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="MassivePaymentController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    
    # Check source file still has other classes
    modified_source = source_file.read_text(encoding="utf-8")
    assert "class AuthController:" in modified_source
    assert "class UserProfileController:" in modified_source
    assert "MassivePaymentController" not in modified_source

def test_transplant_empty_source_file(tmp_path):
    """Should handle empty source file gracefully."""
    source_file = tmp_path / "empty.py"
    source_file.write_text("", encoding="utf-8")
    dest_file = tmp_path / "output.py"
    workspace_dir = str(tmp_path)
    
    result = transplant_class(
        source_file=str(source_file),
        target_class="AnyClass",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert result is False
    assert not dest_file.exists()

def test_transplant_class_with_exception_handling(tmp_path):
    """Should handle classes that catch exceptions (e is ignored)."""
    source_file = tmp_path / "exception_handler.py"
    source_file.write_text(textwrap.dedent("""
        class SafeProcessor:
            def process(self):
                try:
                    return 1 / 0
                except ZeroDivisionError as e:
                    return 0
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "exception_out.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="SafeProcessor",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "class SafeProcessor:" in new_code
    assert "except ZeroDivisionError as e:" in new_code

def test_transplant_class_with_args_kwargs(tmp_path):
    """Should handle classes using *args and **kwargs."""
    source_file = tmp_path / "args_kwargs.py"
    source_file.write_text(textwrap.dedent("""
        class FlexibleHandler:
            def handle(self, *args, **kwargs):
                return sum(args) + sum(kwargs.values())
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "args_kwargs_out.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="FlexibleHandler",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "class FlexibleHandler:" in new_code
    assert "def handle(self, *args, **kwargs):" in new_code

def test_transplant_malformed_source(tmp_path):
    """Should return False for syntactically invalid Python."""
    source_file = tmp_path / "malformed.py"
    source_file.write_text("this is not valid python @@@", encoding="utf-8")
    dest_file = tmp_path / "output.py"
    workspace_dir = str(tmp_path)
    
    result = transplant_class(
        source_file=str(source_file),
        target_class="AnyClass",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert result is False
    assert not dest_file.exists()

def test_transplant_source_file_cleanup(complex_controller_file):
    """Should clean up the source file properly after extraction."""
    source_file = complex_controller_file / "controllers.py"
    dest_file = complex_controller_file / "payment_controller.py"
    workspace_dir = str(complex_controller_file)
    
    original_content = source_file.read_text(encoding="utf-8")
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="MassivePaymentController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    
    modified_source = source_file.read_text(encoding="utf-8")
    # The source should be shorter (class removed)
    assert len(modified_source) < len(original_content)
    # Should still be valid Python
    try:
        compile(modified_source, '<test>', 'exec')
    except SyntaxError:
        pytest.fail("Modified source file is not valid Python")

def test_transplant_multi_name_import_no_stale_self_import(tmp_path):
    """A single 'from X import A, B, C' covering multiple deps must NOT trigger
    a blind auto-heal re-import from the old module."""
    package_dir = tmp_path / "myapp"
    package_dir.mkdir()
    source_file = package_dir / "controllers.py"
    source_file.write_text(textwrap.dedent("""
        from django.core.exceptions import PermissionDenied
        from django.db.models import Q, QuerySet
        from kidecon.users.models import User
        from kidecon.market.models import Catalog, CatalogItem, Category, Product, Vendor

        class CatalogController:
            def get_catalog_items(self, tribe, user_is_chief=False):
                catalog, _ = Catalog.objects.get_or_create(tribe=tribe)
                return CatalogItem.objects.filter(catalog=catalog)

            def get_local_vendors(self, tribe) -> QuerySet[Vendor]:
                return Vendor.objects.filter(is_approved=True)
    """).strip(), encoding="utf-8")

    dest_file = package_dir / "catalog.py"
    workspace_dir = tmp_path

    success = transplant_class(
        source_file=str(source_file),
        target_class="CatalogController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )

    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")

    # 1. Required imports harvested from multi-name lines (unused names pruned)
    assert "from kidecon.market.models import Catalog, CatalogItem, Vendor" in new_code
    assert "from django.db.models import QuerySet" in new_code

    # 2. Unused imports (not a dependency) are correctly excluded
    assert "from kidecon.users.models import User" not in new_code
    assert "PermissionDenied" not in new_code

    # 3. The critical bug: NO stale self-import from the old module
    assert "from myapp.controllers import" not in new_code

    # 4. Class is present
    assert "class CatalogController:" in new_code


def test_transplant_nested_class(tmp_path):
    """Should handle classes that contain nested classes."""
    source_file = tmp_path / "nested.py"
    source_file.write_text(textwrap.dedent("""
        class OuterController:
            class InnerConfig:
                TIMEOUT = 30
            
            def get_config(self):
                return self.InnerConfig.TIMEOUT
    """).strip(), encoding="utf-8")
    
    dest_file = tmp_path / "nested_out.py"
    workspace_dir = str(tmp_path)
    
    success = transplant_class(
        source_file=str(source_file),
        target_class="OuterController",
        dest_file=str(dest_file),
        base_dir=str(workspace_dir)
    )
    
    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")
    assert "class OuterController:" in new_code
    assert "class InnerConfig:" in new_code


# ----------------------------------------------------------------------
# IMPORT HARVESTING & AUTO-HEALING EDGE CASES
# ----------------------------------------------------------------------


def test_dedup_same_module_imports(tmp_path):
    """B. Two separate import lines from the same module produce only one merged line."""
    package_dir = tmp_path / "myapp"
    package_dir.mkdir()
    source_file = package_dir / "controllers.py"
    source_file.write_text(textwrap.dedent("""
        from kidecon.users.models import User, Notification
        from kidecon.users.models import TradePact

        class MyController:
            def do_thing(self, user: User):
                pact = TradePact.objects.filter(user=user).first()
                return pact
    """).strip(), encoding="utf-8")

    dest_file = package_dir / "my_controller.py"

    success = transplant_class(
        source_file=str(source_file),
        target_class="MyController",
        dest_file=str(dest_file),
        base_dir=str(tmp_path)
    )

    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")

    # Should have exactly ONE import from kidecon.users.models
    assert new_code.count("from kidecon.users.models import") == 1
    # Should contain both used names
    assert "User" in new_code
    assert "TradePact" in new_code
    # Unused name should NOT appear
    assert "Notification" not in new_code


def test_prune_unused_names_from_import(tmp_path):
    """C. Only names actually used by the class are in the output import line."""
    source_file = tmp_path / "source.py"
    source_file.write_text(textwrap.dedent("""
        from kidecon.users.constants import ChatMessageRole, TradePactStatus, UserRole

        class StatusChecker:
            def check(self, pact):
                return pact.status == TradePactStatus.ACTIVE
    """).strip(), encoding="utf-8")

    dest_file = tmp_path / "status_checker.py"

    success = transplant_class(
        source_file=str(source_file),
        target_class="StatusChecker",
        dest_file=str(dest_file),
        base_dir=str(tmp_path)
    )

    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")

    # Must contain only the used constant
    assert "TradePactStatus" in new_code
    # Unused piggyback names are stripped
    assert "ChatMessageRole" not in new_code
    assert "UserRole" not in new_code


def test_no_stale_self_import(tmp_path):
    """D. Auto-healer must NOT generate 'from old_module import X'
    when X is already available from a harvested third-party import."""
    package_dir = tmp_path / "myapp"
    package_dir.mkdir()
    source_file = package_dir / "controllers.py"
    source_file.write_text(textwrap.dedent("""
        from myapp.models import Widget

        class WidgetController:
            def get_widget(self):
                return Widget.objects.first()
    """).strip(), encoding="utf-8")

    dest_file = package_dir / "widget_controller.py"

    success = transplant_class(
        source_file=str(source_file),
        target_class="WidgetController",
        dest_file=str(dest_file),
        base_dir=str(tmp_path)
    )

    assert success is True
    new_code = dest_file.read_text(encoding="utf-8")

    # Must have the correct import from models
    assert "from myapp.models import Widget" in new_code
    # Must NOT have a stale circular self-import back to controllers
    assert "from myapp.controllers import" not in new_code


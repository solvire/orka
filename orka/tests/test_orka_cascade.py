import os
import textwrap
import pytest

from orka.core.cascade import cascade_import_updates
from orka.core.ingester import OrkaGraphDB

@pytest.fixture
def mock_django_project(tmp_path):
    """Creates a mock project with views relying on controllers."""
    # Create an apps/billing directory structure
    billing_dir = tmp_path / "apps" / "billing"
    billing_dir.mkdir(parents=True)
    
    controllers = billing_dir / "controllers.py"
    controllers.write_text("class PaymentController:\n    pass\nclass RefundController:\n    pass", encoding="utf-8")
    
    views = billing_dir / "views.py"
    views_code = textwrap.dedent("""
        from apps.billing.controllers import RefundController, PaymentController, InvoiceController

        class View:
            def get(self):
                return PaymentController()
    """).strip()
    views.write_text(views_code, encoding="utf-8")
    
    payment = billing_dir / "payment_controller.py"
    payment.write_text("", encoding="utf-8")
    
    return tmp_path

def test_cascading_import_split(mock_django_project):
    # 1. Build the graph mapping
    cache_path = mock_django_project / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    
    # We pass the root dir so the ingester creates RELATIVE paths (e.g. 'apps/billing/controllers.py')
    db.scan_directory(str(mock_django_project))
    
    old_file = mock_django_project / "apps" / "billing" / "controllers.py"
    new_file = mock_django_project / "apps" / "billing" / "payment_controller.py"
    views_file = mock_django_project / "apps" / "billing" / "views.py"
    
    # 2. Run the cascade
    updated_files = cascade_import_updates(
        graph_db=db,
        target_class="PaymentController",
        old_file_path=str(old_file),
        new_file_path=str(new_file),
        base_dir=str(mock_django_project)
    )
    
    # 3. Verify the cascade triggered successfully
    assert updated_files == 1
    
    # 4. Verify the AST split the imports correctly
    updated_views_code = views_file.read_text(encoding="utf-8")
    
    # The old import should remain, but without PaymentController
    assert "from apps.billing.controllers import RefundController, InvoiceController" in updated_views_code
    assert "PaymentController" not in "from apps.billing.controllers import RefundController, InvoiceController"
    
    # The new import should be added securely
    assert "from apps.billing.payment_controller import PaymentController" in updated_views_code


def test_cascade_no_files_import_old_file(tmp_path):
    """Verifies cascade returns 0 when no files import the old file."""
    # Create a project with a class but no imports of it
    billing_dir = tmp_path / "apps" / "billing"
    billing_dir.mkdir(parents=True)
    
    controllers = billing_dir / "controllers.py"
    controllers.write_text("class PaymentController:\n    pass\n", encoding="utf-8")
    
    views = billing_dir / "views.py"
    views.write_text("class View:\n    pass\n", encoding="utf-8")
    
    payment = billing_dir / "payment_controller.py"
    payment.write_text("", encoding="utf-8")
    
    cache_path = tmp_path / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    db.scan_directory(str(tmp_path))
    
    old_file = billing_dir / "controllers.py"
    new_file = billing_dir / "payment_controller.py"
    
    updated_files = cascade_import_updates(
        graph_db=db,
        target_class="PaymentController",
        old_file_path=str(old_file),
        new_file_path=str(new_file),
        base_dir=str(tmp_path)
    )
    assert updated_files == 0


def test_cascade_multiple_files_import_old_file(tmp_path):
    """Verifies cascade updates multiple files that import the old file."""
    billing_dir = tmp_path / "apps" / "billing"
    billing_dir.mkdir(parents=True)
    
    controllers = billing_dir / "controllers.py"
    controllers.write_text("class PaymentController:\n    pass\nclass RefundController:\n    pass\n", encoding="utf-8")
    
    views1 = billing_dir / "views1.py"
    views1.write_text("from apps.billing.controllers import PaymentController\n\nclass View1:\n    pass\n", encoding="utf-8")
    
    views2 = billing_dir / "views2.py"
    views2.write_text("from apps.billing.controllers import PaymentController, RefundController\n\nclass View2:\n    pass\n", encoding="utf-8")
    
    payment = billing_dir / "payment_controller.py"
    payment.write_text("", encoding="utf-8")
    
    cache_path = tmp_path / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    db.scan_directory(str(tmp_path))
    
    old_file = billing_dir / "controllers.py"
    new_file = billing_dir / "payment_controller.py"
    
    updated_files = cascade_import_updates(
        graph_db=db,
        target_class="PaymentController",
        old_file_path=str(old_file),
        new_file_path=str(new_file),
        base_dir=str(tmp_path)
    )
    assert updated_files == 2
    
    updated_views1 = views1.read_text(encoding="utf-8")
    assert "from apps.billing.payment_controller import PaymentController" in updated_views1
    assert "from apps.billing.controllers import" not in updated_views1
    
    updated_views2 = views2.read_text(encoding="utf-8")
    assert "from apps.billing.payment_controller import PaymentController" in updated_views2
    assert "from apps.billing.controllers import RefundController" in updated_views2


def test_cascade_import_with_alias(tmp_path):
    """Verifies cascade handles imports with 'as' alias (alias is currently dropped)."""
    billing_dir = tmp_path / "apps" / "billing"
    billing_dir.mkdir(parents=True)
    
    controllers = billing_dir / "controllers.py"
    controllers.write_text("class PaymentController:\n    pass\n", encoding="utf-8")
    
    views = billing_dir / "views.py"
    views.write_text("from apps.billing.controllers import PaymentController as PC\n\nclass View:\n    pass\n", encoding="utf-8")
    
    payment = billing_dir / "payment_controller.py"
    payment.write_text("", encoding="utf-8")
    
    cache_path = tmp_path / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    db.scan_directory(str(tmp_path))
    
    old_file = billing_dir / "controllers.py"
    new_file = billing_dir / "payment_controller.py"
    
    updated_files = cascade_import_updates(
        graph_db=db,
        target_class="PaymentController",
        old_file_path=str(old_file),
        new_file_path=str(new_file),
        base_dir=str(tmp_path)
    )
    assert updated_files == 1
    
    updated_views = views.read_text(encoding="utf-8")
    # Current implementation drops the alias; the import becomes without 'as PC'
    assert "from apps.billing.payment_controller import PaymentController" in updated_views
    assert "as PC" not in updated_views
    assert "from apps.billing.controllers import" not in updated_views


def test_cascade_class_not_found_in_graph(tmp_path):
    """Verifies cascade returns 0 when target class is not in the graph."""
    billing_dir = tmp_path / "apps" / "billing"
    billing_dir.mkdir(parents=True)
    
    controllers = billing_dir / "controllers.py"
    controllers.write_text("class PaymentController:\n    pass\n", encoding="utf-8")
    
    views = billing_dir / "views.py"
    views.write_text("from apps.billing.controllers import PaymentController\n\nclass View:\n    pass\n", encoding="utf-8")
    
    payment = billing_dir / "payment_controller.py"
    payment.write_text("", encoding="utf-8")
    
    cache_path = tmp_path / ".orka_cache.json"
    db = OrkaGraphDB(cache_file=str(cache_path))
    db.scan_directory(str(tmp_path))
    
    old_file = billing_dir / "controllers.py"
    new_file = billing_dir / "payment_controller.py"
    
    updated_files = cascade_import_updates(
        graph_db=db,
        target_class="NonExistentClass",
        old_file_path=str(old_file),
        new_file_path=str(new_file),
        base_dir=str(tmp_path)
    )
    assert updated_files == 0

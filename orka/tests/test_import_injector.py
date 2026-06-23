"""Tests for orka.core.import_injector.

Covers the discrete, idempotent primitives (``extract_imports``,
``inject_imports``, ``rewrite_import``, ``dedupe_imports``) and the
high-level orchestrators (``auto_import``, ``resolve_import_for_test``,
``cascade_import_updates``, ``harvest_and_dedupe``).

These tests also absorb the behaviour previously covered by
``test_import_fixer_inject_imports`` (now ``inject_imports``) and
``test_snippet_import_extractor`` (now ``extract_imports``).
"""

from __future__ import annotations

import textwrap

import networkx as nx
import pytest

from orka.core.import_injector import (
    auto_import,
    cascade_import_updates,
    dedupe_imports,
    extract_imports,
    harvest_and_dedupe,
    inject_imports,
    resolve_import_for_test,
    rewrite_import,
)


# ═══════════════════════════════════════════════════════════════════════
# Mock graph DB helper
# ═══════════════════════════════════════════════════════════════════════


class _MockGraphDB:
    """Minimal stand-in for ``OrkaGraphDB`` exposing a ``.graph`` attr."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph


# ═══════════════════════════════════════════════════════════════════════
# extract_imports
# ═══════════════════════════════════════════════════════════════════════


class TestExtractImports:
    def test_no_imports_returns_source_unchanged(self):
        source = "x = 1\ny = 2\n"
        out, imports = extract_imports(source)
        assert out == source
        assert imports == []

    def test_all_imports_extracted(self):
        source = "import os\nimport sys\nfrom x import a, b\ncode = 1\n"
        out, imports = extract_imports(source)
        assert "import os" not in out
        assert "import sys" not in out
        assert "from x import" not in out
        assert "code = 1" in out
        assert set(imports) == {"import os", "import sys", "from x import a, b"}

    def test_extract_is_idempotent(self):
        source = "import os\nx = 1\n"
        out1, imports1 = extract_imports(source)
        out2, imports2 = extract_imports(out1)
        assert out2 == out1
        assert imports2 == []

    def test_filtered_extraction_prunes_to_matching_names(self):
        source = "from x import a, b, c\ncode = 1\n"
        out, imports = extract_imports(source, name_filter={"a", "c"})
        # Only matching names extracted; non-matching left behind (clean)
        assert imports == ["from x import a, c"]
        assert "from x import b" in out
        assert "a" not in out.split("\n")[0]
        assert "c" not in out.split("\n")[0]
        assert "code = 1" in out

    def test_filtered_extraction_no_match_leaves_line_intact(self):
        source = "from x import a, b\ncode = 1\n"
        out, imports = extract_imports(source, name_filter={"z"})
        assert imports == []
        assert "from x import a, b" in out
        assert "code = 1" in out

    def test_filtered_extraction_idempotent(self):
        source = "from x import a, b\ncode = 1\n"
        out1, _ = extract_imports(source, name_filter={"a"})
        out2, imports2 = extract_imports(out1, name_filter={"a"})
        assert out2 == out1
        assert imports2 == []

    def test_import_star_extracted_when_no_filter(self):
        source = "from x import *\ncode = 1\n"
        out, imports = extract_imports(source)
        assert imports == ["from x import *"]
        assert "import *" not in out
        assert "code = 1" in out

    def test_import_star_kept_when_filtered(self):
        # A star import cannot be partitioned by name -> left intact.
        source = "from x import *\ncode = 1\n"
        out, imports = extract_imports(source, name_filter={"a"})
        assert imports == []
        assert "from x import *" in out

    def test_mixed_import_and_code_line(self):
        # ``import os; x = 1`` -> import extracted, code kept.
        source = "import os; x = 1\n"
        out, imports = extract_imports(source)
        assert imports == ["import os"]
        assert out.strip() == "x = 1"

    def test_mixed_fromimport_and_code_line(self):
        source = "from os import path; x = 1\n"
        out, imports = extract_imports(source)
        assert imports == ["from os import path"]
        assert out.strip() == "x = 1"

    def test_empty_source(self):
        out, imports = extract_imports("")
        assert out == ""
        assert imports == []

    def test_syntax_error_returns_source_unchanged(self):
        source = "def broken(\n"
        out, imports = extract_imports(source)
        assert out == source
        assert imports == []

    def test_bare_dotted_import_extracted(self):
        source = "import os.path\ncode = 1\n"
        out, imports = extract_imports(source)
        assert imports == ["import os.path"]
        assert "code = 1" in out


# ═══════════════════════════════════════════════════════════════════════
# inject_imports
# ═══════════════════════════════════════════════════════════════════════


class TestInjectImports:
    def test_inject_into_empty_source(self):
        result = inject_imports("", {})
        assert result == ""

    def test_inject_single_bare_import(self):
        result = inject_imports("x = 1", {"os": ("os", None)})
        assert "import os" in result
        assert "x = 1" in result

    def test_inject_with_object(self):
        result = inject_imports("print('hello')", {"path": ("os", "path")})
        assert "from os import path" in result
        assert "print('hello')" in result

    def test_inject_multiple_imports(self):
        result = inject_imports(
            "result = sqrt(4)",
            {"math": ("math", None), "sqrt": ("math", "sqrt")},
        )
        assert "import math" in result
        assert "from math import sqrt" in result
        assert "result = sqrt(4)" in result

    def test_inject_preserves_existing_imports(self):
        result = inject_imports("import os\nx = 1", {"sys": ("sys", None)})
        assert "import os" in result
        assert "import sys" in result
        assert "x = 1" in result

    def test_inject_duplicate_not_added_twice(self):
        result = inject_imports("import os\nx = 1", {"os": ("os", None)})
        assert result.count("import os") == 1
        assert "x = 1" in result

    def test_inject_idempotent(self):
        imports = {"os": ("os", None), "path": ("os", "path")}
        once = inject_imports("x = 1", imports)
        twice = inject_imports(once, imports)
        assert once == twice

    def test_inject_empty_imports_dict_returns_source(self):
        source = "x = 1"
        assert inject_imports(source, {}) == source

    def test_inject_syntax_error_returns_source(self):
        source = "def broken("
        assert inject_imports(source, {"os": ("os", None)}) == source

    def test_inject_multiline_source(self):
        source = "def foo():\n    return 42\n\nx = foo()"
        result = inject_imports(source, {"math": ("math", None)})
        assert "import math" in result
        assert "def foo():" in result
        assert "x = foo()" in result

    def test_inject_source_with_comments(self):
        source = "# comment\nx = 1"
        result = inject_imports(source, {"os": ("os", None)})
        assert "# comment" in result
        assert "x = 1" in result

    def test_inject_after_future_imports(self):
        source = "from __future__ import annotations\nx = 1"
        result = inject_imports(source, {"os": ("os", None)})
        assert "from __future__ import annotations" in result
        assert "import os" in result

    def test_inject_preserves_relative_imports(self):
        source = "from . import utils\nx = 1"
        result = inject_imports(source, {"os": ("os", None)})
        assert "from . import utils" in result
        assert "import os" in result


# ═══════════════════════════════════════════════════════════════════════
# rewrite_import
# ═══════════════════════════════════════════════════════════════════════


class TestRewriteImport:
    def test_simple_rewrite(self):
        source = "from old import Target\nx = 1\n"
        result = rewrite_import(source, "old", "new", "Target")
        assert "from new import Target" in result
        assert "from old import" not in result
        assert "x = 1" in result

    def test_multi_name_import_split(self):
        source = "from old import A, B, C\nx = 1\n"
        result = rewrite_import(source, "old", "new", "B")
        assert "from old import A, C" in result
        assert "from new import B" in result
        assert "x = 1" in result

    def test_multi_name_only_target(self):
        # When the target is the only name, the whole line is swapped.
        source = "from old import Target\nx = 1\n"
        result = rewrite_import(source, "old", "new", "Target")
        assert "from new import Target" in result
        assert "from old import" not in result

    def test_old_module_not_found_is_noop(self):
        source = "from other import Target\nx = 1\n"
        result = rewrite_import(source, "old", "new", "Target")
        assert result == source

    def test_already_rewritten_is_idempotent(self):
        source = "from new import Target\nx = 1\n"
        result = rewrite_import(source, "old", "new", "Target")
        assert result == source

    def test_rewrite_idempotent_on_output(self):
        source = "from old import A, B, C\nx = 1\n"
        once = rewrite_import(source, "old", "new", "B")
        twice = rewrite_import(once, "old", "new", "B")
        assert once == twice

    def test_rewrite_alias_dropped(self):
        # Aliases on the target are dropped (matches old cascade behaviour).
        source = "from old import Target as T\nx = 1\n"
        result = rewrite_import(source, "old", "new", "Target")
        assert "from new import Target" in result
        assert "as T" not in result

    def test_rewrite_relative_import_untouched(self):
        # Only absolute imports matching old_module are rewritten.
        source = "from .old import Target\nx = 1\n"
        result = rewrite_import(source, "old", "new", "Target")
        assert "from .old import Target" in result

    def test_rewrite_syntax_error_returns_source(self):
        source = "def broken(\n"
        assert rewrite_import(source, "old", "new", "Target") == source


# ═══════════════════════════════════════════════════════════════════════
# dedupe_imports
# ═══════════════════════════════════════════════════════════════════════


class TestDedupeImports:
    def test_empty_list(self):
        assert dedupe_imports([]) == []

    def test_merge_same_module_imports(self):
        result = dedupe_imports(["from x import a", "from x import b"])
        assert result == ["from x import a, b"]

    def test_remove_duplicates(self):
        result = dedupe_imports(["from x import a", "from x import a"])
        assert result == ["from x import a"]

    def test_sort_names_alphabetically(self):
        result = dedupe_imports(
            ["from kidecon.market.models import Catalog, CatalogItem, Vendor"]
        )
        assert result == ["from kidecon.market.models import Catalog, CatalogItem, Vendor"]

    def test_merge_and_sort_across_lines(self):
        # dedupe_imports merges ALL names given to it (pruning by required
        # deps is the job of harvest_and_dedupe, not this primitive).
        result = dedupe_imports(
            [
                "from kidecon.users.models import User, Notification",
                "from kidecon.users.models import TradePact",
            ]
        )
        assert result == ["from kidecon.users.models import Notification, TradePact, User"]

    def test_bare_imports_sorted_first(self):
        result = dedupe_imports(["from x import a", "import os", "import abc"])
        assert result == ["import abc", "import os", "from x import a"]

    def test_dedupe_bare_import_duplicates(self):
        result = dedupe_imports(["import os", "import os", "import sys"])
        assert result == ["import os", "import sys"]

    def test_dedupe_idempotent(self):
        once = dedupe_imports(["from x import a", "from x import b"])
        twice = dedupe_imports(once)
        assert once == twice

    def test_dedupe_with_alias(self):
        result = dedupe_imports(["from x import a as A", "from x import b"])
        assert result == ["from x import a as A, b"]

    def test_dedupe_skips_star_imports(self):
        # Star imports cannot be merged by name; they are skipped.
        result = dedupe_imports(["from x import *", "from x import a"])
        assert result == ["from x import a"]

    def test_dedupe_relative_imports_grouped(self):
        result = dedupe_imports(["from . import a", "from . import b"])
        assert result == ["from . import a, b"]


# ═══════════════════════════════════════════════════════════════════════
# auto_import
# ═══════════════════════════════════════════════════════════════════════


class TestAutoImport:
    def test_no_undefined_names_is_noop(self):
        source = "import os\nos.getcwd()\n"
        assert auto_import(source) == source

    def test_injects_stdlib_fallback(self):
        # ``os`` is undefined -> stdlib fallback emits ``import os``.
        source = "print(os.getcwd())\n"
        result = auto_import(source)
        assert "import os" in result
        assert "print(os.getcwd())" in result

    def test_idempotent(self):
        source = "print(os.getcwd())\n"
        once = auto_import(source)
        twice = auto_import(once)
        assert once == twice

    def test_with_graph_db_resolves_symbol(self):
        g = nx.DiGraph()
        g.add_node(
            "Function:pkg.helper",
            node_type="function",
            name="helper",
            file_path="pkg/helper.py",
            lineno=1,
        )
        source = "x = helper()\n"
        result = auto_import(source, graph_db=_MockGraphDB(g))
        assert "from pkg import helper" in result
        assert "x = helper()" in result

    def test_syntax_error_returns_source(self):
        source = "def broken(\n"
        assert auto_import(source) == source


# ═══════════════════════════════════════════════════════════════════════
# resolve_import_for_test
# ═══════════════════════════════════════════════════════════════════════


class TestResolveImportForTest:
    def test_class_name(self):
        result = resolve_import_for_test(
            "src/payments/processor.py", class_name="OrderProcessor"
        )
        assert result == "from src.payments.processor import OrderProcessor\n"

    def test_method_name(self):
        result = resolve_import_for_test(
            "src/payments/processor.py", method_name="process_payment"
        )
        assert result == "from src.payments.processor import process_payment\n"

    def test_class_takes_precedence_over_method(self):
        result = resolve_import_for_test(
            "src/payments/processor.py",
            class_name="OrderProcessor",
            method_name="reject",
        )
        assert "OrderProcessor" in result
        assert "reject" not in result

    def test_none_when_resolution_fails(self):
        assert resolve_import_for_test("") is None

    def test_none_when_no_name_given(self):
        assert resolve_import_for_test("src/x.py") is None

    def test_with_graph_db(self):
        g = nx.DiGraph()
        g.add_node(
            "Method:pkg.Handler.handle",
            node_type="method",
            name="handle",
            file_path="pkg/handler.py",
            lineno=5,
        )
        result = resolve_import_for_test(
            "pkg/handler.py",
            class_name="Handler",
            method_name="handle",
            graph_db=_MockGraphDB(g),
        )
        assert result == "from pkg import Handler\n"


# ═══════════════════════════════════════════════════════════════════════
# cascade_import_updates
# ═══════════════════════════════════════════════════════════════════════


class TestCascadeImportUpdates:
    @pytest.fixture
    def cascade_project(self, tmp_path):
        """A project where views.py imports a class from controllers.py."""
        billing = tmp_path / "apps" / "billing"
        billing.mkdir(parents=True)
        (billing / "controllers.py").write_text(
            "class PaymentController:\n    pass\nclass RefundController:\n    pass\n",
            encoding="utf-8",
        )
        (billing / "views.py").write_text(
            textwrap.dedent(
                """
                from apps.billing.controllers import RefundController, PaymentController, InvoiceController

                class View:
                    def get(self):
                        return PaymentController()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        (billing / "payment_controller.py").write_text("", encoding="utf-8")
        return tmp_path

    def _build_graph(self, project_dir):
        from orka.core.ingester import OrkaGraphDB

        cache = project_dir / ".orka_cache.json"
        db = OrkaGraphDB(cache_file=str(cache))
        db.scan_directory(str(project_dir))
        return db

    def test_cascade_rewrites_dependent_file(self, cascade_project):
        db = self._build_graph(cascade_project)
        old_file = cascade_project / "apps" / "billing" / "controllers.py"
        new_file = cascade_project / "apps" / "billing" / "payment_controller.py"
        views = cascade_project / "apps" / "billing" / "views.py"

        updated = cascade_import_updates(
            graph_db=db,
            target_class="PaymentController",
            old_file_path=str(old_file),
            new_file_path=str(new_file),
            base_dir=str(cascade_project),
        )
        assert updated == 1

        code = views.read_text(encoding="utf-8")
        assert "from apps.billing.controllers import RefundController, InvoiceController" in code
        assert "from apps.billing.payment_controller import PaymentController" in code

    def test_cascade_no_dependents_returns_zero(self, tmp_path):
        billing = tmp_path / "apps" / "billing"
        billing.mkdir(parents=True)
        (billing / "controllers.py").write_text(
            "class PaymentController:\n    pass\n", encoding="utf-8"
        )
        (billing / "views.py").write_text("class View:\n    pass\n", encoding="utf-8")
        (billing / "payment_controller.py").write_text("", encoding="utf-8")

        db = self._build_graph(tmp_path)
        updated = cascade_import_updates(
            graph_db=db,
            target_class="PaymentController",
            old_file_path=str(billing / "controllers.py"),
            new_file_path=str(billing / "payment_controller.py"),
            base_dir=str(tmp_path),
        )
        assert updated == 0

    def test_cascade_target_not_in_graph_returns_zero(self, cascade_project):
        db = self._build_graph(cascade_project)
        old_file = cascade_project / "apps" / "billing" / "controllers.py"
        new_file = cascade_project / "apps" / "billing" / "payment_controller.py"

        updated = cascade_import_updates(
            graph_db=db,
            target_class="NonExistentClass",
            old_file_path=str(old_file),
            new_file_path=str(new_file),
            base_dir=str(cascade_project),
        )
        assert updated == 0


# ═══════════════════════════════════════════════════════════════════════
# harvest_and_dedupe
# ═══════════════════════════════════════════════════════════════════════


class TestHarvestAndDedupe:
    def test_extracts_and_merges_matching_imports(self):
        source = textwrap.dedent(
            """
            from kidecon.users.models import User, Notification
            from kidecon.users.models import TradePact

            class MyController:
                def do_thing(self, user: User):
                    pact = TradePact.objects.filter(user=user).first()
                    return pact
            """
        ).strip()
        result = harvest_and_dedupe(source, {"User", "TradePact"})
        assert result == ["from kidecon.users.models import TradePact, User"]

    def test_prunes_unused_names_from_multiname_import(self):
        source = textwrap.dedent(
            """
            from kidecon.market.models import Catalog, CatalogItem, Category, Product, Vendor

            class CatalogController:
                def get_items(self):
                    return CatalogItem.objects.filter(catalog=Catalog)
                def vendors(self) -> Vendor:
                    return Vendor.objects.first()
            """
        ).strip()
        result = harvest_and_dedupe(source, {"Catalog", "CatalogItem", "Vendor"})
        assert result == ["from kidecon.market.models import Catalog, CatalogItem, Vendor"]

    def test_no_matching_deps_returns_empty(self):
        source = "import os\nimport sys\nclass C:\n    pass\n"
        assert harvest_and_dedupe(source, {"NotPresent"}) == []

    def test_empty_deps_returns_empty(self):
        source = "import os\nclass C:\n    pass\n"
        assert harvest_and_dedupe(source, set()) == []

    def test_idempotent(self):
        source = "from x import a, b\nclass C:\n    pass\n"
        once = harvest_and_dedupe(source, {"a"})
        # Re-running on a fresh source (the function is stateless) is stable.
        twice = harvest_and_dedupe(source, {"a"})
        assert once == twice
        assert once == ["from x import a"]

    def test_merges_across_modules(self):
        source = textwrap.dedent(
            """
            from django.db.models import Q, QuerySet
            from kidecon.market.models import Catalog, CatalogItem, Vendor

            class C:
                def m(self) -> QuerySet[Vendor]:
                    return CatalogItem.objects.filter(catalog=Catalog)
            """
        ).strip()
        result = harvest_and_dedupe(
            source, {"Catalog", "CatalogItem", "Vendor", "QuerySet"}
        )
        assert result == [
            "from django.db.models import QuerySet",
            "from kidecon.market.models import Catalog, CatalogItem, Vendor",
        ]

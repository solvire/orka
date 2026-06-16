import libcst as cst
import pytest
from orka.surgery.modifier import SnippetImportExtractor


def test_leave_SimpleStatementLine_removes_import_only_line():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("import os\nx = 1\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 1
    assert isinstance(transformer.extracted_imports[0], cst.Import)
    assert modified_module.code.strip() == "x = 1"


def test_leave_SimpleStatementLine_keeps_non_import_statements():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("x = 1\ny = 2\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 0
    assert modified_module.code.strip() == "x = 1\ny = 2"


def test_leave_SimpleStatementLine_handles_mixed_import_and_code():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("import os; x = 1\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 1
    assert isinstance(transformer.extracted_imports[0], cst.Import)
    assert modified_module.code.strip() == "x = 1"


def test_leave_SimpleStatementLine_handles_import_from():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("from os import path\nx = 1\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 1
    assert isinstance(transformer.extracted_imports[0], cst.ImportFrom)
    assert modified_module.code.strip() == "x = 1"


def test_leave_SimpleStatementLine_handles_multiple_imports():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("import os\nimport sys\nx = 1\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 2
    assert all(isinstance(imp, cst.Import) for imp in transformer.extracted_imports)
    assert modified_module.code.strip() == "x = 1"


def test_leave_SimpleStatementLine_handles_empty_body_after_extraction():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("import os\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 1
    assert modified_module.code.strip() == ""


def test_leave_SimpleStatementLine_handles_no_imports():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("x = 1\ny = 2\nz = 3\n")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 0
    assert modified_module.code.strip() == "x = 1\ny = 2\nz = 3"


def test_leave_SimpleStatementLine_handles_empty_module():
    transformer = SnippetImportExtractor()
    module = cst.parse_module("")
    modified_module = module.visit(transformer)
    assert len(transformer.extracted_imports) == 0
    assert modified_module.code.strip() == ""

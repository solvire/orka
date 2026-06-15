"""Tests for CLI commands – prompt and testgen flows (scaffold)."""

import pytest
from click.testing import CliRunner

from orka.cli import app as cli


class TestPromptCommand:
    def test_basic_prompt(self):
        """Prompt command runs without error with basic arguments."""
        runner = CliRunner()
        result = runner.invoke(cli, ["prompt", "--template", "refactor", "--code", "def f(): pass"])
        assert result.exit_code == 0
        assert "def f():" in result.output or "def f():" in result.output  # placeholder

    def test_prompt_unknown_option(self):
        """Unrecognised option should produce an error."""
        runner = CliRunner()
        result = runner.invoke(cli, ["prompt", "--nonexistent", "value"])
        assert result.exit_code != 0


class TestTestgenCommand:
    def test_testgen_basic(self):
        """testgen command runs without error."""
        runner = CliRunner()
        result = runner.invoke(cli, ["testgen", "--code", "def add(a,b): return a+b"])
        assert result.exit_code == 0

    def test_testgen_requires_code(self):
        """testgen should fail if no code is provided."""
        runner = CliRunner()
        result = runner.invoke(cli, ["testgen"])
        assert result.exit_code != 0
        assert "Error" in result.output

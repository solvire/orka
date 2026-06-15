"""Tests for CLI commands – prompt and testgen flows (scaffold)."""

import pytest
from click.testing import CliRunner
from typer.main import get_command

from orka.cli import app as cli


class TestPromptCommand:
    def test_basic_prompt(self):
        """Prompt command runs without error with basic arguments."""
        runner = CliRunner()
        click_cli = get_command(cli)
        result = runner.invoke(click_cli, ["prompt", "--template", "refactor"])
        assert result.exit_code == 0
        assert "Template: refactor" in result.output

    def test_prompt_unknown_option(self):
        """Unrecognised option should produce an error."""
        runner = CliRunner()
        click_cli = get_command(cli)
        result = runner.invoke(click_cli, ["prompt", "--nonexistent", "value"])
        assert result.exit_code != 0


class TestTestgenCommand:
    def test_testgen_file_not_found(self):
        """testgen should fail if the provided file does not exist."""
        runner = CliRunner()
        click_cli = get_command(cli)
        result = runner.invoke(click_cli, ["testgen", "--file", "nonexistent.py", "--method", "add"])
        assert result.exit_code != 0
        assert "File not found" in result.output

    def test_testgen_missing_required_options(self):
        """testgen should fail if required options are missing."""
        runner = CliRunner()
        click_cli = get_command(cli)
        result = runner.invoke(click_cli, ["testgen"])
        assert result.exit_code != 0
        assert "Missing option" in result.output

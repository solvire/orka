# orka/core/snippet_utils.py
"""
Centralized utility for sanitizing LLM output.

Provides a single function ``sanitize_llm_output`` that both the modifier
and generator modules use to clean up raw LLM responses before further
processing.
"""

from __future__ import annotations

import re
import textwrap
from typing import Optional

def strip_md_fences(text: str) -> str:
    """Safely removes markdown code fences from LLM output."""
    text = text.strip()
    if not text:
        return text
        
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove the opening fence (e.g., ```python)
        lines = lines[1:]
        # Remove the closing fence if it exists
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
        
    return text.strip()

def normalize_snippet_indent(text: str) -> str:
    """
    Fixes indentation for snippets where the first line is flush-left
    but subsequent lines are deeply indented (common in LLM outputs 
    or triple-quoted test strings).
    """
    text = text.strip("\n")
    lines = text.splitlines()
    
    if not lines:
        return ""
    
    # Compute the minimum indentation across all lines
    indents = []
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        indents.append(indent)
    min_indent = min(indents) if indents else 0
    
    result_lines = []
    for line in lines:
        stripped = line.lstrip()
        current_indent = len(line) - len(stripped)
        relative_indent = current_indent - min_indent
        # Reduce by one level (4 spaces) if there is more than one level of indentation
        if relative_indent > 4:
            relative_indent -= 4
        result_lines.append(' ' * relative_indent + stripped)
    
    return '\n'.join(result_lines).strip()

def sanitize_llm_output(raw: str) -> str:
    """Multi-pass sanitization of untrusted LLM output.

    Performs the following cleaning steps in order:

    1. Strip leading/trailing whitespace.
    2. Remove markdown code fences (`` ```python `` etc.) and any
       surrounding text outside the fences.
    3. Remove any leading/trailing blank lines.
    4. Un-indent the code to the base level (using ``textwrap.dedent``).
    5. Remove any trailing whitespace on each line.
    6. Remove any remaining blank lines at the very start or end.

    Parameters
    ----------
    raw
        The raw string returned by the LLM.

    Returns
    -------
    str
        Cleaned Python code. May be empty if nothing recoverable was found.
    """
    if not raw:
        return ""

    # 1. Strip leading/trailing whitespace
    cleaned = raw.strip()

    # 2. Remove markdown code fences and any surrounding text
    #    Pattern matches ```...``` blocks (with optional language label)
    #    and extracts the content inside.
    fence_pattern = r"```(?:\w+)?\s*\n?(.*?)\n?```"
    match = re.search(fence_pattern, cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()

    # 3. Remove leading/trailing blank lines
    lines = cleaned.splitlines()
    # Strip leading blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    # Strip trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = "\n".join(lines)

    # 4. Un-indent to base level
    try:
        cleaned = textwrap.dedent(cleaned)
    except ValueError:
        # textwrap.dedent can raise if the string has inconsistent indentation
        # (e.g., mixed tabs and spaces). In that case, just strip leading
        # whitespace from each line.
        lines = cleaned.splitlines()
        cleaned_lines = [line.lstrip() for line in lines]
        cleaned = "\n".join(cleaned_lines)

    # 5. Remove trailing whitespace on each line
    lines = cleaned.splitlines()
    cleaned_lines = [line.rstrip() for line in lines]
    cleaned = "\n".join(cleaned_lines)

    # 6. Remove any remaining blank lines at start/end
    while cleaned.startswith("\n"):
        cleaned = cleaned[1:]
    while cleaned.endswith("\n"):
        cleaned = cleaned[:-1]

    return cleaned

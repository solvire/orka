"""
Rule resolver — loads, merges, and sorts injection rules for the compiler.

Three-tier override hierarchy
-----------------------------
1. **Tier 1 (Builtin)** — Rules shipped with Orka in ``orka/prompts/rules/builtin/``.
2. **Tier 2 (Project)** — Rules from the user's ``.orka/rules/`` directory.
   If a project rule has the same ``name`` as a builtin rule, the project
   version wins (overrides the builtin).
3. **Tier 3 (CLI)** — Rules passed explicitly via ``--rule`` CLI flags.
   These always win over any lower-tier rule with the same name.

Deterministic sorting
---------------------
After merging, rules are sorted by ``(priority, -tier, name)`` so that
CI/CD pipelines always produce the exact same prompt string for the same
inputs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Optional

import yaml

from orka.core.templates import InjectionPoint, InjectionRule

logger = logging.getLogger(__name__)


# ===================================================================
# Constants
# ===================================================================

# Default directories (relative to Orka package root)
BUILTIN_RULES_DIR = Path(__file__).resolve().parent.parent / "prompts" / "rules" / "builtin"
PROJECT_RULES_DIRNAME = Path(".orka") / "rules"

# YAML frontmatter delimiter pattern for .mdc files
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n*(.*)", re.DOTALL)


# ===================================================================
# .mdc file parser
# ===================================================================


def parse_mdc_file(file_path: Path) -> Optional[InjectionRule]:
    """Parse a ``.mdc`` file into an :class:`InjectionRule`.

    ``.mdc`` files use the standard Markdown-with-YAML-frontmatter format
    (adopted by Continue.dev and Cursor). The frontmatter block is delimited
    by ``---`` lines at the very start of the file.

    Expected frontmatter keys
    -------------------------
    ``name`` : str
        Unique rule name (maps to :attr:`InjectionRule.name`).
    ``description`` : str, optional
        Human-readable summary.
    ``injection_point`` : str
        Must match one of the :class:`InjectionPoint` enum values.
    ``applies_to`` : list[str] or str, optional
        List of template names, or a single name. Defaults to ``["*"]``.
    ``priority`` : int, optional
        Numeric priority (lower = more important). Defaults to ``100``.

    The remaining content (everything after the closing ``---``) becomes
    the rule's ``text`` field.

    Parameters
    ----------
    file_path
        Path to a ``.mdc`` file on disk.

    Returns
    -------
    InjectionRule or None
        ``None`` if the file cannot be read or parsed.

    Examples
    --------
    >>> rule = parse_mdc_file(Path("test_behavior.mdc"))
    >>> rule.name
    'test_behavior_not_mocks'
    >>> rule.injection_point
    <InjectionPoint.quality_gates: 'quality_gates'>
    """
    if not file_path.exists():
        logger.warning("Rule file not found: %s", file_path)
        return None
    if not file_path.suffix == ".mdc":
        logger.warning("Expected .mdc file, got: %s", file_path)
        return None

    raw = file_path.read_text(encoding="utf-8")

    # Match frontmatter
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        logger.warning("Missing or malformed frontmatter in: %s", file_path)
        return None

    frontmatter_raw = match.group(1)
    body = match.group(2).strip()

    # Parse YAML frontmatter
    try:
        meta = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as exc:
        logger.warning("YAML parse error in %s: %s", file_path, exc)
        return None

    if not isinstance(meta, dict):
        logger.warning("Frontmatter is not a mapping in: %s", file_path)
        return None

    name = meta.get("name")
    if not name:
        logger.warning("Missing 'name' in frontmatter: %s", file_path)
        return None

    # Normalise injection_point
    ip_raw = meta.get("injection_point", "constraints_top")
    try:
        injection_point = InjectionPoint(ip_raw)
    except ValueError:
        logger.warning(
            "Invalid injection_point %r in %s — falling back to 'constraints_top'",
            ip_raw,
            file_path,
        )
        injection_point = InjectionPoint.constraints_top

    # Normalise applies_to
    applies_to_raw = meta.get("applies_to", ["*"])
    if isinstance(applies_to_raw, str):
        applies_to = [applies_to_raw]
    elif isinstance(applies_to_raw, list):
        applies_to = [str(item) for item in applies_to_raw]
    else:
        applies_to = ["*"]

    # Parse priority
    try:
        priority = int(meta.get("priority", 100))
    except (ValueError, TypeError):
        priority = 100

    description = str(meta.get("description", ""))

    return InjectionRule(
        name=str(name),
        description=description,
        injection_point=injection_point,
        text=body,
        applies_to=applies_to,
        priority=priority,
    )


# ===================================================================
# Directory loaders
# ===================================================================


def load_rules_from_directory(directory: Path, tier: int = 1) -> list[InjectionRule]:
    """Load all ``.mdc`` files from *directory* as :class:`InjectionRule` instances.

    Non-``.mdc`` files are silently skipped. The *tier* parameter is
    assigned to every rule loaded from this directory (see the three-tier
    override hierarchy).

    Parameters
    ----------
    directory
        Path to a directory containing ``.mdc`` rule files.
    tier
        The tier number to assign to all loaded rules (1=builtin,
        2=project, 3=CLI).

    Returns
    -------
    list[InjectionRule]
        Rules loaded from the directory. Malformed files are logged and
        skipped (never raise).
    """
    if not directory.is_dir():
        logger.debug("Rule directory does not exist: %s", directory)
        return []

    rules: list[InjectionRule] = []
    for fpath in sorted(directory.iterdir()):
        if fpath.suffix != ".mdc":
            continue
        rule = parse_mdc_file(fpath)
        if rule is not None:
            rule.tier = tier
            rules.append(rule)
            logger.debug("Loaded rule %r (tier %d) from %s", rule.name, tier, fpath)

    return rules


# ===================================================================
# CLI rule loader
# ===================================================================


def load_rules_by_name(
    names: Iterable[str],
    candidates: list[InjectionRule],
) -> list[InjectionRule]:
    """Resolve rule names from a known pool of candidates (Tier 3).

    For each name in *names*, find the matching rule in *candidates*.
    The *candidates* list should contain **both** builtin and project rules
    so that a CLI flag can reference any known rule by name.

    Parameters
    ----------
    names
        Rule names to look up (e.g. from ``--rule`` CLI flags).
    candidates
        Pool of known rules (builtin + project).

    Returns
    -------
    list[InjectionRule]
        Matched rules with ``tier`` set to 3. Unknown names are logged
        and skipped.
    """
    name_map = {r.name: r for r in candidates}
    resolved: list[InjectionRule] = []

    for name in names:
        rule = name_map.get(name)
        if rule is None:
            logger.warning("CLI rule %r not found in any tier — skipping.", name)
            continue
        # Return a shallow copy with tier overridden
        resolved.append(rule.model_copy(update={"tier": 3}))

    return resolved


# ===================================================================
# Resolution algorithm
# ===================================================================


def resolve_rules(
    template_name: str,
    injection_points: list[InjectionPoint],
    *,
    builtin_rules_dir: Optional[Path] = None,
    project_rules_dir: Optional[Path] = None,
    cli_rule_names: Optional[list[str]] = None,
) -> list[InjectionRule]:
    """Load, merge, filter, and sort rules for a given template.

    This is the central "Resolution Algorithm" from the architecture design.
    It enforces the three-tier override hierarchy, filters by ``applies_to``,
    and produces a deterministic sort order.

    Parameters
    ----------
    template_name
        The name of the template being compiled (e.g. ``"refactor"``).
        Used to filter rules by their ``applies_to`` list.
    injection_points
        The list of injection points that the template supports.
        Rules targeting other injection points are discarded.
    builtin_rules_dir
        Override the default builtin rules directory. If ``None``, uses
        ``BUILTIN_RULES_DIR``.
    project_rules_dir
        Path to the project-level ``.orka/rules/`` directory. If ``None``,
        no project rules are loaded.
    cli_rule_names
        List of rule names passed via ``--rule`` CLI flags.
        If ``None``, no CLI rules are resolved.

    Returns
    -------
    list[InjectionRule]
        A deterministic list of rules ready for injection into the
        template, sorted by ``(priority, -tier, name)``.

    Algorithm
    ---------
    1. **Load** all rules from each tier.
    2. **Merge** into a single ``name -> rule`` map, applying the
       override hierarchy (CLI > Project > Builtin).
    3. **Filter** by ``applies_to`` — only keep rules whose ``applies_to``
       list contains the ``template_name`` or the wildcard ``"*"``.
    4. **Filter** by ``injection_point`` — only keep rules whose
       injection point is in the template's supported points.
    5. **Sort** by ``(priority, -tier, name)`` for deterministic output.
    """
    # ---- Step 1: Load ----
    builtin_dir = builtin_rules_dir or BUILTIN_RULES_DIR
    builtin_rules = load_rules_from_directory(builtin_dir, tier=1)
    project_rules = load_rules_from_directory(project_rules_dir, tier=2) if project_rules_dir else []

    # Candidate pool for CLI resolution
    all_known = builtin_rules + project_rules
    cli_rules = load_rules_by_name(cli_rule_names or [], all_known)

    # ---- Step 2: Merge with override hierarchy ----
    # Iterate in order of increasing priority: builtin → project → CLI
    rule_map: dict[str, InjectionRule] = {}

    for rule in builtin_rules:
        rule_map[rule.name] = rule

    for rule in project_rules:
        rule_map[rule.name] = rule  # overwrites builtin

    for rule in cli_rules:
        rule_map[rule.name] = rule  # overwrites everything

    # ---- Step 3: Filter by applies_to ----
    applicable: list[InjectionRule] = []
    for rule in rule_map.values():
        if template_name in rule.applies_to or "*" in rule.applies_to:
            applicable.append(rule)

    # ---- Step 4: Filter by injection_point ----
    supported_points = set(injection_points)
    filtered = [r for r in applicable if r.injection_point in supported_points]

    # ---- Step 5: Deterministic sort ----
    filtered.sort(key=lambda r: (r.priority, -r.tier, r.name))

    logger.debug(
        "Resolved %d rules for template %r (from %d builtin, %d project, %d CLI, "
        "%d filtered by applies_to, %d by injection_point)",
        len(filtered),
        template_name,
        len(builtin_rules),
        len(project_rules),
        len(cli_rules),
        len(applicable),
        len(filtered),
    )

    return filtered

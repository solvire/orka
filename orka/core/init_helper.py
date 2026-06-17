"""
Orka Initialization — runs once per project to configure AI tool rules,
validate the environment, and persist status to .orka/status.json.

Design
------
Status is stored in <project_root>/.orka/status.json so that every subsequent
orka command can check whether init has been completed.

The .orka/ directory is git-ignored by default (added to .gitignore on init).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from orka.config import settings
from orka.core.constants import (
    DEFAULT_EDITOR_TEMPLATE,
    EDITOR_PROMPT_CHOICES,
    EDITOR_REGISTRY,
    PROVIDER_KEY_ATTR_MAP,
    SUPPORTED_PROVIDERS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

logger = logging.getLogger("orka.init")

# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

STATUS_DIR = ".orka"
STATUS_FILE = "status.json"


def _status_path(project_root: Optional[Path] = None) -> Path:
    root = project_root or settings.PROJECT_ROOT
    return root / STATUS_DIR / STATUS_FILE


def load_status(project_root: Optional[Path] = None) -> Dict:
    """Load the .orka/status.json file, returning {} if missing."""
    path = _status_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Corrupt status file at {path}, ignoring.")
        return {}


def save_status(data: Dict, project_root: Optional[Path] = None) -> None:
    """Write data to .orka/status.json, merging with existing keys."""
    path = _status_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = load_status(project_root)
    existing.update(data)
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()

    path.write_text(json.dumps(existing, indent=2, default=str))
    logger.info(f"Status saved to {path}")


def is_initialized(project_root: Optional[Path] = None) -> bool:
    """Return True if init has been completed."""
    status = load_status(project_root)
    return status.get("initialized", False)


def show_init_notice(console: Console, command: str) -> None:
    """Print a warning if the project hasn't been initialized."""
    if is_initialized():
        return
    console.print(
        Panel.fit(
            "[bold yellow]⚠  Not initialized[/bold yellow]\n\n"
            f"Run [bold cyan]orka init[/bold cyan] first to configure rules for your AI coding tool.\n"
            f"Then run [bold cyan]orka {command}[/bold cyan] again.",
            border_style="yellow",
        )
    )


# ---------------------------------------------------------------------------
# .gitignore management
# ---------------------------------------------------------------------------

GITIGNORE_ENTRY = "\n# Orka local state\n.orka/\n"


def _ensure_gitignore(project_root: Path) -> None:
    """Add .orka/ to .gitignore if not already present."""
    gitignore = project_root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_ENTRY.lstrip())
        return

    content = gitignore.read_text()
    if ".orka/" not in content:
        gitignore.write_text(content.rstrip() + GITIGNORE_ENTRY)


# ---------------------------------------------------------------------------
# Rule writer
# ---------------------------------------------------------------------------

def _resolve_template(editor: str = "") -> Optional[Path]:
    """
    Find the template file for the given editor.
    Works whether orka is installed editable or from source.
    """
    rel_path = (
        EDITOR_REGISTRY.get(editor, {}).get("template_path") or DEFAULT_EDITOR_TEMPLATE
    )

    # Try relative to this file (works for editable install)
    here = Path(__file__).resolve().parent  # orka/core/
    pkg_root = here.parent.parent  # orka/ source root
    candidate = pkg_root / rel_path
    if candidate.exists():
        return candidate

    # Try relative to the installed package
    import orka
    inst_root = Path(orka.__file__).resolve().parent.parent
    candidate = inst_root / rel_path
    if candidate.exists():
        return candidate

    return None


def _write_rule(target_path: Path, template_path: Path) -> bool:
    """Copy the template to the target path, skipping if content matches."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    template_content = template_path.read_text(encoding="utf-8")

    if target_path.exists():
        existing = target_path.read_text(encoding="utf-8")
        if existing.strip() == template_content.strip():
            logger.info(f"Rule already up-to-date: {target_path}")
            return False

    target_path.write_text(template_content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------

def _check_provider(provider: str) -> Tuple[bool, str]:
    """Check if the chosen provider has an API key set."""
    key_attr = PROVIDER_KEY_ATTR_MAP.get(provider, "")
    if key_attr:
        val = getattr(settings, key_attr, "")
        if not val:
            return False, f"No {key_attr} found in .env"

    return True, f"Provider '{provider}' ready"


def _detect_existing_providers() -> List[str]:
    """Return list of providers that already have API keys configured."""
    available = []
    for prov in SUPPORTED_PROVIDERS:
        key_attr = PROVIDER_KEY_ATTR_MAP.get(prov, "")
        if key_attr and getattr(settings, key_attr, ""):
            available.append(prov)
    return available


# ---------------------------------------------------------------------------
# Main init flow
# ---------------------------------------------------------------------------

def run_init(
    editor: Optional[str] = None,
    provider: Optional[str] = None,
    force: bool = False,
) -> bool:
    """
    Run the interactive (or flag-driven) initialization.

    Args:
        editor: One of the EDITOR_REGISTRY keys, or None for prompt.
        provider: One of SUPPORTED_PROVIDERS, or None for prompt.
        force: Re-write rule even if already present.

    Returns:
        True if initialization succeeded, False otherwise.
    """
    console = Console()
    project_root = settings.PROJECT_ROOT

    # -- Already initialized? --
    if is_initialized() and not force:
        console.print(
            Panel.fit(
                "[bold green]✓  Already initialized[/bold green]\n\n"
                f"Status file: {_status_path(project_root)}\n"
                f"Run [bold cyan]orka init --force[/bold cyan] to re-initialize.",
                border_style="green",
            )
        )
        return True

    console.print(Panel.fit("[bold cyan]Orka Initialization[/bold cyan]", border_style="cyan"))
    console.print()

    # -- Step 1: Editor selection --
    editor_slugs = list(EDITOR_REGISTRY.keys())

    if editor and editor not in EDITOR_REGISTRY:
        console.print(f"[bold red]Unknown editor '{editor}'.[/bold red]")
        console.print(f"Supported: {', '.join(editor_slugs)}")
        return False

    if not editor:
        console.print("[bold]Which AI coding tool are you using?[/bold]")
        for key, (name, _) in EDITOR_PROMPT_CHOICES.items():
            console.print(f"  [{key}] {name}")
        choice = Prompt.ask("Select", choices=list(EDITOR_PROMPT_CHOICES.keys()), default="1")
        editor_name, rel_path = EDITOR_PROMPT_CHOICES[choice]
        editor = editor_slugs[int(choice) - 1]
    else:
        cfg = EDITOR_REGISTRY[editor]
        editor_name = cfg["display_name"]
        rel_path = cfg["rule_path"]

    # -- Step 2: Write the rule file --
    template_path = _resolve_template(editor)
    if not template_path:
        console.print("[bold red]Template file not found![/bold red]")
        expected_rel = EDITOR_REGISTRY.get(editor, {}).get("template_path") or DEFAULT_EDITOR_TEMPLATE
        console.print(f"Expected at: {expected_rel} relative to package root")
        return False

    target_path = project_root / rel_path
    written = _write_rule(target_path, template_path)

    if written:
        console.print(f"[green]✓[/green] Rule written to [bold]{rel_path}[/bold]")
    else:
        console.print(f"[dim]✓ Rule already up-to-date: {rel_path}[/dim]")

    # -- Step 3: Provider selection --
    available_providers = _detect_existing_providers()

    if not provider:
        if available_providers:
            console.print()
            console.print("[bold]Detected API keys for:[/bold] " + ", ".join(available_providers))
            use_existing = Confirm.ask(
                f"Use [bold]{available_providers[0]}[/bold] as default provider?",
                default=True,
            )
            if use_existing:
                provider = available_providers[0]
            else:
                provider = Prompt.ask(
                    "Choose provider",
                    choices=SUPPORTED_PROVIDERS,
                    default=settings.DEFAULT_PROVIDER,
                )
        else:
            console.print()
            console.print("[bold]No API keys detected in .env[/bold]")
            console.print("Choose your default provider (you can set the key later):")
            provider = Prompt.ask(
                "Provider",
                choices=SUPPORTED_PROVIDERS,
                default=settings.DEFAULT_PROVIDER,
            )
    else:
        if provider not in SUPPORTED_PROVIDERS:
            console.print(f"[bold red]Unknown provider '{provider}'.[/bold red]")
            console.print(f"Supported: {', '.join(SUPPORTED_PROVIDERS)}")
            return False

    # -- Step 4: Validate provider --
    ok, msg = _check_provider(provider)
    if ok:
        console.print(f"[green]✓[/green] {msg}")
    else:
        console.print(f"[yellow]⚠  {msg}[/yellow]")
        console.print("  You can set it in .env and re-run [bold]orka init[/bold]")

    # -- Step 5: Update settings.DEFAULT_PROVIDER to match --
    if provider != settings.DEFAULT_PROVIDER:
        console.print(f"[dim]Default provider set to '{provider}' in status (not persisted to .env)[/dim]")

    # -- Step 6: Save status --
    status = {
        "initialized": True,
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "editor": editor_name,
        "rule_path": rel_path,
        "provider": provider,
        "last_scan": None,
    }
    save_status(status)

    # -- Step 7: Ensure .gitignore --
    _ensure_gitignore(project_root)

    # -- Done --
    console.print()
    console.print(
        Panel.fit(
            "[bold green]✓  Orka initialized successfully[/bold green]\n\n"
            f"Editor : {editor_name}\n"
            f"Rule   : {rel_path}\n"
            f"Default: {provider}\n\n"
            "Next: [bold cyan]orka scan[/bold cyan] to build the dependency graph.",
            border_style="green",
        )
    )
    return True
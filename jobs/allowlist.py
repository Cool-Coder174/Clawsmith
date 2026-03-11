"""Command allowlist for ClawSmith job execution."""

from __future__ import annotations

import re

from config.config_loader import get_config

SHELL_METACHARACTERS = re.compile(r"[&|<>^;]")

DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "cursor",
        "python",
        "pip",
        "npm",
        "npx",
        "node",
        "cargo",
        "dotnet",
        "git",
        "pytest",
        "ruff",
        "mypy",
        "eslint",
        "tsc",
    }
)

DEFAULT_ALLOWED_MULTI_TOKEN: tuple[tuple[str, ...], ...] = (
    ("cmd", "/c"),
)


def get_effective_allowlist() -> frozenset[str]:
    """Merge the default allowlist with any config-defined entries."""
    config_commands = get_config().execution.allowed_commands
    if not config_commands:
        return DEFAULT_ALLOWED_COMMANDS
    extras = frozenset(cmd.lower() for cmd in config_commands)
    return DEFAULT_ALLOWED_COMMANDS | extras


def _normalize_token(token: str) -> str:
    """Strip path separators and lowercase a command token."""
    return token.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()


def validate_command(cmd: str, allowlist: frozenset[str] | None = None) -> bool:
    """Check whether *cmd* is safe and its base executable is in the allowlist.

    Rejects commands containing shell metacharacters or chaining operators
    (``&``, ``&&``, ``|``, ``||``, redirection, ``;``).  Supports multi-token
    allowlist patterns like ``cmd /c``.
    """
    if allowlist is None:
        allowlist = get_effective_allowlist()

    tokens = cmd.strip().split()
    if not tokens:
        return False

    if SHELL_METACHARACTERS.search(cmd):
        return False

    first = _normalize_token(tokens[0])

    for pattern in DEFAULT_ALLOWED_MULTI_TOKEN:
        if len(tokens) >= len(pattern):
            normalized = tuple(_normalize_token(t) for t in tokens[: len(pattern)])
            if normalized == pattern:
                return True

    return first in allowlist

"""Validate specs against codebase — heuristic string matching.

This module checks if spec features/criteria appear in the codebase
using simple content matching. For LLM-powered semantic verification,
use orchestrator/verifier.py instead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def validate_spec_against_code(
    spec_path: str | Path,
    codebase_path: str | Path,
) -> tuple[bool, str]:
    """Check if spec features and acceptance criteria appear in the codebase.

    Returns (is_valid, message).
    """
    with open(spec_path, 'r', encoding='utf-8') as f:
        spec = json.load(f)

    features = spec.get('features', [])
    acceptance_criteria = spec.get('acceptance_criteria', [])

    missing_features = _find_missing_items(features, codebase_path)
    missing_criteria = _find_missing_items(acceptance_criteria, codebase_path)

    errors = []
    if missing_features:
        errors.append(f"Missing features: {', '.join(missing_features)}")
    if missing_criteria:
        errors.append(f"Missing acceptance criteria: {', '.join(missing_criteria)}")

    if errors:
        return False, "❌ " + "; ".join(errors)
    return True, "✅ Spec validated successfully (all features found in code)"


def _find_missing_items(
    items: list[str],
    codebase_path: str | Path,
) -> list[str]:
    """Find items that don't appear in any file in the codebase."""
    root = Path(codebase_path).resolve()

    missing = []
    for item in items:
        item_lower = item.lower().strip()
        if not item_lower:
            continue

        found = False
        for file_path in _iter_code_files(root):
            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                if item_lower in content.lower():
                    found = True
                    break
            except Exception:
                continue

        if not found:
            missing.append(item)

    return missing


def _iter_code_files(root: Path):
    """Yield code/text files from the codebase, skipping common ignore dirs."""
    IGNORE_DIRS = {
        '.git', '.venv', 'venv', 'node_modules', '__pycache__',
        '.pytest_cache', '.mypy_cache', '.ruff_cache', '.clawsmith',
        'dist', 'build', '.egg-info', 'logs', '.next',
    }
    IGNORE_EXTENSIONS = {
        '.pyc', '.pyo', '.so', '.dll', '.dylib', '.bin', '.exe',
        '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp',
        '.mp3', '.mp4', '.wav', '.avi', '.mov',
        '.zip', '.tar', '.gz', '.rar', '.7z',
    }

    for path in root.rglob('*'):
        if path.is_dir():
            if path.name in IGNORE_DIRS or any(
                p in path.parts for p in IGNORE_DIRS
            ):
                continue
            continue

        if path.suffix.lower() in IGNORE_EXTENSIONS:
            continue

        yield path

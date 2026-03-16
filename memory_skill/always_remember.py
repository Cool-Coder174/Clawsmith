"""Always-remember — durable cross-session memory annotations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

log = get_logger("memory.always_remember")


def _memory_id(content: str, category: str) -> str:
    raw = f"{category}:{content}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class AlwaysRemember:
    """Manages durable cross-session memory entries.

    Entries persist in .clawsmith/always_remember/ as JSON files.
    They survive across sessions and repos.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._dir = (workspace_root / ".clawsmith" / "always_remember").resolve()

    @property
    def storage_dir(self) -> Path:
        return self._dir

    def remember(
        self,
        content: str,
        category: str = "note",
        tags: list[str] | None = None,
        repo_path: str = "",
    ) -> str:
        """Store a new always-remember entry. Returns the entry ID."""
        self._dir.mkdir(parents=True, exist_ok=True)
        entry_id = _memory_id(content, category)
        entry = {
            "id": entry_id,
            "content": content,
            "category": category,
            "tags": tags or [],
            "repo_path": repo_path,
            "created_at": datetime.now(UTC).isoformat(),
        }
        path = self._dir / f"{entry_id}.json"
        path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        log.info("Stored always-remember: %s (%s)", entry_id, category)
        return entry_id

    def forget(self, entry_id: str) -> bool:
        """Remove an always-remember entry by ID."""
        path = self._dir / f"{entry_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_entries(self) -> list[dict[str, Any]]:
        """List all always-remember entries."""
        entries: list[dict[str, Any]] = []
        if not self._dir.exists():
            return entries

        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append(data)
            except Exception as exc:
                log.warning("Failed to read %s: %s", path, exc)
        return entries

    def search(self, query: str) -> list[dict[str, Any]]:
        """Search entries by content/tag matching."""
        q = query.lower()
        results = []
        for entry in self.list_entries():
            content = entry.get("content", "").lower()
            tags = [t.lower() for t in entry.get("tags", [])]
            if q in content or any(q in t for t in tags):
                results.append(entry)
        return results

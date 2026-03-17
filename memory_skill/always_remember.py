"""Always-remember — durable, typed, cross-session memory with promotion and decay.

Each entry carries typed dimensions so the retriever can rank it against a
task without dumping everything into context:

- **repo** / **workspace** — spatial scope
- **dependency_stack** — language/framework tags
- **workflow_type** — build, test, lint, deploy, …
- **task_category** — bugfix, refactor, debug, …
- **created_at** / **last_accessed_at** — recency
- **hit_count** / **accept_count** / **usefulness_score** — acceptance
- **suppressed** — explicit noise suppression
"""

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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AlwaysRemember:
    """Manages durable, typed cross-session memory entries.

    Storage: ``.clawsmith/always_remember/*.json``
    """

    def __init__(self, workspace_root: Path) -> None:
        self._dir = (workspace_root / ".clawsmith" / "always_remember").resolve()

    @property
    def storage_dir(self) -> Path:
        return self._dir

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        category: str = "note",
        tags: list[str] | None = None,
        repo_path: str = "",
        *,
        workspace: str = "",
        dependency_stack: list[str] | None = None,
        workflow_type: str = "",
        task_category: str = "",
    ) -> str:
        """Store a new entry. Returns the entry ID."""
        self._dir.mkdir(parents=True, exist_ok=True)
        entry_id = _memory_id(content, category)
        now = _now_iso()
        entry: dict[str, Any] = {
            "id": entry_id,
            "content": content,
            "category": category,
            "tags": tags or [],
            "repo_path": repo_path,
            "workspace": workspace,
            "dependency_stack": dependency_stack or [],
            "workflow_type": workflow_type,
            "task_category": task_category,
            "created_at": now,
            "last_accessed_at": now,
            "hit_count": 0,
            "accept_count": 0,
            "usefulness_score": 0.0,
            "suppressed": False,
        }
        self._write(entry_id, entry)
        log.info("Stored always-remember: %s (%s)", entry_id, category)
        return entry_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, entry_id: str) -> dict[str, Any] | None:
        """Load a single entry by ID.  Returns ``None`` if absent."""
        path = self._dir / f"{entry_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_entries(self, *, include_suppressed: bool = False) -> list[dict[str, Any]]:
        """List entries.  Suppressed entries are excluded by default."""
        entries: list[dict[str, Any]] = []
        if not self._dir.exists():
            return entries

        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not include_suppressed and data.get("suppressed", False):
                    continue
                entries.append(data)
            except Exception as exc:
                log.warning("Failed to read %s: %s", path, exc)
        return entries

    def search(self, query: str) -> list[dict[str, Any]]:
        """Search non-suppressed entries by content/tag matching."""
        q = query.lower()
        results = []
        for entry in self.list_entries():
            content = entry.get("content", "").lower()
            tags = [t.lower() for t in entry.get("tags", [])]
            if q in content or any(q in t for t in tags):
                results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Delete / suppress
    # ------------------------------------------------------------------

    def forget(self, entry_id: str) -> bool:
        """Permanently remove an entry."""
        path = self._dir / f"{entry_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def suppress(self, entry_id: str) -> bool:
        """Mark an entry as suppressed so the ranker will skip it.

        The entry is **not** deleted — it can be unsuppressed later.
        """
        entry = self.get(entry_id)
        if entry is None:
            return False
        entry["suppressed"] = True
        self._write(entry_id, entry)
        log.info("Suppressed memory: %s", entry_id)
        return True

    def unsuppress(self, entry_id: str) -> bool:
        """Remove the suppression flag from an entry."""
        entry = self.get(entry_id)
        if entry is None:
            return False
        entry["suppressed"] = False
        self._write(entry_id, entry)
        return True

    # ------------------------------------------------------------------
    # Promotion — accepted task outcomes → durable memory
    # ------------------------------------------------------------------

    def promote_outcome(
        self,
        content: str,
        category: str = "accepted_outcome",
        tags: list[str] | None = None,
        repo_path: str = "",
        *,
        workspace: str = "",
        dependency_stack: list[str] | None = None,
        workflow_type: str = "",
        task_category: str = "",
        usefulness_score: float = 0.8,
    ) -> str:
        """Promote an accepted task outcome into durable memory.

        If an entry with the same content+category already exists, its
        ``accept_count`` and ``usefulness_score`` are incremented instead
        of creating a duplicate.
        """
        entry_id = _memory_id(content, category)
        existing = self.get(entry_id)

        if existing is not None:
            existing["accept_count"] = existing.get("accept_count", 0) + 1
            existing["hit_count"] = existing.get("hit_count", 0) + 1
            old_score = existing.get("usefulness_score", 0.0)
            existing["usefulness_score"] = min(old_score + 0.1, 1.0)
            existing["last_accessed_at"] = _now_iso()
            existing["suppressed"] = False
            self._write(entry_id, existing)
            log.info("Promoted existing memory %s (accept=%d)", entry_id, existing["accept_count"])
            return entry_id

        self._dir.mkdir(parents=True, exist_ok=True)
        now = _now_iso()
        entry: dict[str, Any] = {
            "id": entry_id,
            "content": content,
            "category": category,
            "tags": tags or [],
            "repo_path": repo_path,
            "workspace": workspace,
            "dependency_stack": dependency_stack or [],
            "workflow_type": workflow_type,
            "task_category": task_category,
            "created_at": now,
            "last_accessed_at": now,
            "hit_count": 1,
            "accept_count": 1,
            "usefulness_score": usefulness_score,
            "suppressed": False,
        }
        self._write(entry_id, entry)
        log.info("Promoted new outcome to memory: %s (%s)", entry_id, category)
        return entry_id

    # ------------------------------------------------------------------
    # Acceptance tracking (called at retrieval time)
    # ------------------------------------------------------------------

    def record_hit(self, entry_id: str) -> None:
        """Increment ``hit_count`` and update ``last_accessed_at``."""
        entry = self.get(entry_id)
        if entry is None:
            return
        entry["hit_count"] = entry.get("hit_count", 0) + 1
        entry["last_accessed_at"] = _now_iso()
        self._write(entry_id, entry)

    def record_accept(self, entry_id: str) -> None:
        """Increment ``accept_count`` and boost ``usefulness_score``."""
        entry = self.get(entry_id)
        if entry is None:
            return
        entry["accept_count"] = entry.get("accept_count", 0) + 1
        old = entry.get("usefulness_score", 0.0)
        entry["usefulness_score"] = min(old + 0.1, 1.0)
        entry["last_accessed_at"] = _now_iso()
        self._write(entry_id, entry)

    # ------------------------------------------------------------------
    # Decay — suppress entries that are never useful
    # ------------------------------------------------------------------

    def decay(self, *, min_hits: int = 5, max_reject_ratio: float = 0.8) -> list[str]:
        """Auto-suppress entries that have been retrieved often but rarely accepted.

        Returns the IDs of entries that were suppressed.

        An entry is suppressed when:
        - ``hit_count >= min_hits`` — it has been seen enough times
        - ``accept_count / hit_count < (1 - max_reject_ratio)`` — almost
          never accepted
        """
        suppressed_ids: list[str] = []
        for entry in self.list_entries(include_suppressed=False):
            hits = entry.get("hit_count", 0)
            if hits < min_hits:
                continue
            accepts = entry.get("accept_count", 0)
            ratio = accepts / hits
            if ratio < (1 - max_reject_ratio):
                eid = entry["id"]
                self.suppress(eid)
                suppressed_ids.append(eid)
                log.info(
                    "Auto-suppressed memory %s (hits=%d, accepts=%d, ratio=%.2f)",
                    eid, hits, accepts, ratio,
                )
        return suppressed_ids

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, entry_id: str, data: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{entry_id}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

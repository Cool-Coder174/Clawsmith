"""Mutation engine — propose, stage, validate, approve, apply, and rollback config changes.

Implements the guarded mutation pipeline: changes are staged in a
temporary directory, diffed against the original, validated for safety,
and only applied after explicit approval.  Every mutation is recorded
in an audit log so rollbacks are always possible.
"""

from __future__ import annotations

import fnmatch
import json
from datetime import datetime
from pathlib import Path

from orchestrator.logging_setup import get_logger

from .models import (
    AuditEntry,
    MutationPolicy,
    MutationProposal,
    MutationStatus,
)

log = get_logger("mutation_engine")


class MutationEngine:
    """Controlled mutation system for OpenClaw.

    Every change flows through: propose -> stage -> validate -> approve -> apply.
    Each step is gated by :class:`MutationPolicy`.
    """

    def __init__(
        self,
        workspace_root: Path,
        policy: MutationPolicy | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.staging_dir = workspace_root / ".clawsmith" / "staging"
        self.audit_log_path = workspace_root / ".clawsmith" / "mutation-audit.json"
        self.proposals_dir = workspace_root / ".clawsmith" / "proposals"
        self.backups_dir = workspace_root / ".clawsmith" / "backups"
        self.policy = policy or MutationPolicy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(self, proposal: MutationProposal) -> MutationProposal:
        """Register a new mutation proposal after validating against policy."""
        if not self.policy.self_mutation_enabled:
            raise PermissionError(
                "Self-mutation is disabled by policy. "
                "Set self_mutation_enabled=True to allow proposals."
            )

        allowed, reason = self._check_policy(proposal)
        if not allowed:
            proposal.status = MutationStatus.rejected
            proposal.error = reason
            self._save_proposal(proposal)
            self._append_audit(
                AuditEntry(
                    proposal_id=proposal.id,
                    action="rejected",
                    actor="policy",
                    details=reason,
                )
            )
            log.warning("Proposal %s rejected by policy: %s", proposal.id, reason)
            return proposal

        proposal.status = MutationStatus.proposed
        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal.id,
                action="proposed",
                actor=proposal.requested_by,
                details=proposal.reason,
            )
        )
        log.info("Proposal %s registered (%s)", proposal.id, proposal.mutation_type)
        return proposal

    def stage(self, proposal_id: str) -> MutationProposal:
        """Copy proposed changes into the staging directory."""
        proposal = self._load_proposal(proposal_id)

        if proposal.status != MutationStatus.proposed:
            raise ValueError(
                f"Cannot stage proposal {proposal_id}: "
                f"status is '{proposal.status}', expected 'proposed'"
            )

        stage_dir = self.staging_dir / proposal_id
        stage_dir.mkdir(parents=True, exist_ok=True)

        for rel_path, content in proposal.after_snapshot.items():
            target = stage_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        proposal.status = MutationStatus.staged
        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal_id,
                action="staged",
                details=f"Staged {len(proposal.after_snapshot)} file(s)",
            )
        )
        log.info("Proposal %s staged to %s", proposal_id, stage_dir)
        return proposal

    def validate(self, proposal_id: str) -> MutationProposal:
        """Run validation checks on a staged proposal."""
        proposal = self._load_proposal(proposal_id)

        if proposal.status != MutationStatus.staged:
            raise ValueError(
                f"Cannot validate proposal {proposal_id}: "
                f"status is '{proposal.status}', expected 'staged'"
            )

        errors: list[str] = []

        for rel_path, expected_before in proposal.before_snapshot.items():
            real_path = self.workspace_root / rel_path
            if not real_path.exists():
                errors.append(f"File not found: {rel_path}")
                continue
            current = real_path.read_text(encoding="utf-8")
            if current != expected_before:
                errors.append(
                    f"File {rel_path} has been modified since the proposal was created"
                )

        for rel_path, content in proposal.after_snapshot.items():
            if rel_path.endswith((".yaml", ".yml")):
                try:
                    import yaml  # noqa: F811

                    yaml.safe_load(content)
                except Exception as exc:
                    errors.append(f"Invalid YAML in {rel_path}: {exc}")
            elif rel_path.endswith(".json"):
                try:
                    json.loads(content)
                except Exception as exc:
                    errors.append(f"Invalid JSON in {rel_path}: {exc}")

        if errors:
            proposal.status = MutationStatus.failed
            proposal.validation_result = "; ".join(errors)
            proposal.error = proposal.validation_result
            log.warning("Proposal %s failed validation: %s", proposal_id, errors)
        else:
            proposal.status = MutationStatus.validated
            proposal.validated_at = datetime.now().isoformat()
            proposal.validation_result = "ok"
            log.info("Proposal %s validated successfully", proposal_id)

        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal_id,
                action="validated" if not errors else "validation_failed",
                details=proposal.validation_result or "",
            )
        )
        return proposal

    def approve(
        self, proposal_id: str, actor: str = "user"
    ) -> MutationProposal:
        """Mark a validated proposal as approved (user consent step)."""
        proposal = self._load_proposal(proposal_id)

        acceptable = {MutationStatus.validated}
        if not self.policy.require_validation:
            acceptable.add(MutationStatus.staged)
        if not self.policy.require_staging:
            acceptable.add(MutationStatus.proposed)

        if proposal.status not in acceptable:
            raise ValueError(
                f"Cannot approve proposal {proposal_id}: "
                f"status is '{proposal.status}', expected one of {sorted(acceptable)}"
            )

        proposal.status = MutationStatus.approved
        proposal.approved_at = datetime.now().isoformat()
        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal_id,
                action="approved",
                actor=actor,
            )
        )
        log.info("Proposal %s approved by %s", proposal_id, actor)
        return proposal

    def apply(self, proposal_id: str) -> MutationProposal:
        """Write approved changes to their actual target files."""
        proposal = self._load_proposal(proposal_id)

        if proposal.status != MutationStatus.approved:
            raise ValueError(
                f"Cannot apply proposal {proposal_id}: "
                f"status is '{proposal.status}', expected 'approved'"
            )

        backup_dir = self.backups_dir / proposal_id
        backup_dir.mkdir(parents=True, exist_ok=True)

        for rel_path in proposal.after_snapshot:
            real_path = self.workspace_root / rel_path
            if real_path.exists():
                backup_target = backup_dir / rel_path
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                backup_target.write_text(
                    real_path.read_text(encoding="utf-8"), encoding="utf-8"
                )

        for rel_path, content in proposal.after_snapshot.items():
            real_path = self.workspace_root / rel_path
            real_path.parent.mkdir(parents=True, exist_ok=True)
            real_path.write_text(content, encoding="utf-8")

        proposal.status = MutationStatus.applied
        proposal.applied_at = datetime.now().isoformat()
        proposal.rollback_instructions = (
            f"Backups stored in {backup_dir.relative_to(self.workspace_root)}"
        )
        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal_id,
                action="applied",
                details=f"Applied {len(proposal.after_snapshot)} file(s)",
            )
        )
        log.info("Proposal %s applied", proposal_id)
        return proposal

    def rollback(self, proposal_id: str) -> MutationProposal:
        """Restore original files from backup."""
        proposal = self._load_proposal(proposal_id)

        if proposal.status != MutationStatus.applied:
            raise ValueError(
                f"Cannot rollback proposal {proposal_id}: "
                f"status is '{proposal.status}', expected 'applied'"
            )

        backup_dir = self.backups_dir / proposal_id
        if not backup_dir.exists():
            raise FileNotFoundError(
                f"Backup directory not found for proposal {proposal_id}"
            )

        for rel_path in proposal.after_snapshot:
            backup_path = backup_dir / rel_path
            real_path = self.workspace_root / rel_path
            if backup_path.exists():
                real_path.write_text(
                    backup_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            elif real_path.exists():
                real_path.unlink()

        proposal.status = MutationStatus.rolled_back
        proposal.rolled_back_at = datetime.now().isoformat()
        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal_id,
                action="rolled_back",
                details=f"Restored {len(proposal.after_snapshot)} file(s) from backup",
            )
        )
        log.info("Proposal %s rolled back", proposal_id)
        return proposal

    def reject(self, proposal_id: str, reason: str = "") -> MutationProposal:
        """Reject a proposal at any pre-applied stage."""
        proposal = self._load_proposal(proposal_id)

        if proposal.status in {MutationStatus.applied, MutationStatus.rolled_back}:
            raise ValueError(
                f"Cannot reject proposal {proposal_id}: "
                f"status is '{proposal.status}'"
            )

        proposal.status = MutationStatus.rejected
        proposal.error = reason or "Rejected by user"
        self._save_proposal(proposal)
        self._append_audit(
            AuditEntry(
                proposal_id=proposal_id,
                action="rejected",
                details=reason,
            )
        )
        log.info("Proposal %s rejected: %s", proposal_id, reason)
        return proposal

    def list_proposals(
        self, status: MutationStatus | None = None
    ) -> list[MutationProposal]:
        """List all proposals, optionally filtered by status."""
        if not self.proposals_dir.exists():
            return []

        proposals: list[MutationProposal] = []
        for path in sorted(self.proposals_dir.glob("*.json")):
            try:
                proposal = MutationProposal.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if status is None or proposal.status == status:
                    proposals.append(proposal)
            except Exception:
                log.warning("Skipping unreadable proposal file: %s", path)
        return proposals

    def get_proposal(self, proposal_id: str) -> MutationProposal | None:
        """Load a single proposal by ID, returning ``None`` if not found."""
        try:
            return self._load_proposal(proposal_id)
        except FileNotFoundError:
            return None

    def get_audit_log(self) -> list[AuditEntry]:
        """Read the full audit log."""
        if not self.audit_log_path.exists():
            return []
        try:
            raw = json.loads(self.audit_log_path.read_text(encoding="utf-8"))
            return [AuditEntry.model_validate(entry) for entry in raw]
        except Exception:
            log.warning("Failed to read audit log at %s", self.audit_log_path)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_proposal(self, proposal: MutationProposal) -> Path:
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        path = self.proposals_dir / f"{proposal.id}.json"
        path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        return path

    def _load_proposal(self, proposal_id: str) -> MutationProposal:
        path = self.proposals_dir / f"{proposal_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No proposal found with id '{proposal_id}'")
        return MutationProposal.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def _append_audit(self, entry: AuditEntry) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        if self.audit_log_path.exists():
            try:
                entries = json.loads(
                    self.audit_log_path.read_text(encoding="utf-8")
                )
            except Exception:
                pass
        entries.append(entry.model_dump())
        self.audit_log_path.write_text(
            json.dumps(entries, indent=2), encoding="utf-8"
        )

    def _check_policy(self, proposal: MutationProposal) -> tuple[bool, str]:
        """Validate a proposal against the current mutation policy."""
        if proposal.mutation_type in self.policy.restricted_types:
            return False, f"Mutation type '{proposal.mutation_type}' is restricted"

        if proposal.mutation_type not in self.policy.allowed_types:
            return False, f"Mutation type '{proposal.mutation_type}' is not allowed"

        if len(proposal.affected_files) > self.policy.max_affected_files:
            return False, (
                f"Too many affected files ({len(proposal.affected_files)}), "
                f"max is {self.policy.max_affected_files}"
            )

        for file_path in proposal.affected_files:
            for pattern in self.policy.restricted_paths:
                if fnmatch.fnmatch(file_path, pattern):
                    return False, (
                        f"File '{file_path}' matches restricted pattern '{pattern}'"
                    )

        return True, ""

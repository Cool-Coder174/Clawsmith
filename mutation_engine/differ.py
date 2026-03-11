"""Diff generator — produces unified diffs for staged mutation proposals."""

from __future__ import annotations

import difflib

from orchestrator.logging_setup import get_logger

from .models import MutationProposal

log = get_logger("mutation_engine.differ")


class MutationDiffer:
    """Utility for generating unified diffs and human-readable summaries."""

    @staticmethod
    def generate_diff(before: str, after: str, filename: str = "") -> str:
        """Generate a unified diff between *before* and *after* content."""
        from_label = f"a/{filename}" if filename else "a"
        to_label = f"b/{filename}" if filename else "b"
        diff_lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
        return "".join(diff_lines)

    @staticmethod
    def generate_proposal_diff(proposal: MutationProposal) -> str:
        """Generate a combined diff across all files in a proposal."""
        parts: list[str] = []
        all_files = set(proposal.before_snapshot) | set(proposal.after_snapshot)

        for rel_path in sorted(all_files):
            before = proposal.before_snapshot.get(rel_path, "")
            after = proposal.after_snapshot.get(rel_path, "")
            if before == after:
                continue
            diff = MutationDiffer.generate_diff(before, after, filename=rel_path)
            if diff:
                parts.append(diff)

        return "\n".join(parts)

    @staticmethod
    def format_change_summary(proposal: MutationProposal) -> str:
        """Generate a human-readable change summary for a proposal."""
        lines: list[str] = [
            f"Mutation Proposal: {proposal.id}",
            f"  Type:         {proposal.mutation_type}",
            f"  Requested by: {proposal.requested_by}",
            f"  Status:       {proposal.status}",
        ]

        if proposal.reason:
            lines.append(f"  Reason:       {proposal.reason}")

        if proposal.target_scope:
            lines.append(f"  Scope:        {proposal.target_scope}")

        if proposal.affected_files:
            lines.append(f"  Files ({len(proposal.affected_files)}):")
            for fpath in proposal.affected_files:
                before = fpath in proposal.before_snapshot
                after = fpath in proposal.after_snapshot
                if after and not before:
                    marker = "[new]"
                elif before and not after:
                    marker = "[delete]"
                else:
                    marker = "[modify]"
                lines.append(f"    {marker} {fpath}")

        if proposal.change_summary:
            lines.append(f"  Summary:      {proposal.change_summary}")

        return "\n".join(lines)

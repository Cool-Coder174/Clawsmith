from __future__ import annotations

from pathlib import Path

from orchestrator.schemas import ContextPacket
from tools.context_packer import ContextPacker
from tools.repo_auditor import RepoAuditor
from tools.repo_mapper import RepoMapper

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def _pack(token_budget: int = 8000, file_list: list[str] | None = None, root: Path = FIXTURE_REPO):
    audit = RepoAuditor(root).audit()
    repo_map = RepoMapper(root).map()
    return ContextPacker(root, token_budget=token_budget).pack(
        audit, repo_map, "Fix the login bug", file_list=file_list
    )


def test_produces_valid_context_packet():
    packet = _pack()
    assert isinstance(packet, ContextPacket)
    assert packet.task_summary == "Fix the login bug"
    assert packet.token_estimate > 0


def test_architecture_summary_populated():
    packet = _pack()
    assert packet.architecture_summary


def test_token_budget_truncation():
    packet = _pack(token_budget=50)
    assert packet.token_estimate <= 200


def test_explicit_file_list_respected(tmp_repo):
    packet = _pack(file_list=["src/main.py"], root=tmp_repo)
    assert "src/main.py" in packet.relevant_files


def test_build_commands_detected():
    packet = _pack()
    assert len(packet.build_test_commands) > 0


def test_constraints_populated():
    packet = _pack()
    assert len(packet.constraints) > 0

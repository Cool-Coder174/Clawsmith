from __future__ import annotations

from pydantic import BaseModel


class LLMBundle(BaseModel):
    """A downloadable local-LLM package with hardware requirements."""

    model_id: str
    display_name: str
    family: str
    parameter_count: str
    quantization: str
    runtime: str
    context_size: int
    specialization: str
    estimated_disk_gb: float
    estimated_ram_gb: float
    estimated_vram_gb: float | None
    recommended_use_cases: list[str]
    download_url: str | None = None
    checksum_url: str | None = None
    notes: str = ""


class RecommendationResult(BaseModel):
    """The output of the recommendation engine."""

    primary: LLMBundle
    lighter: LLMBundle | None
    heavier: LLMBundle | None
    explanations: dict[str, str]
    hardware_tier: str
    machine_summary: str

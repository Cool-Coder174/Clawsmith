"""Recommendation engine — suggests local LLMs that fit the user's hardware.

Filters the model catalog by available VRAM/RAM, ranks candidates by
fitness, and returns an ordered list so the user can install the best
model for their machine without manual research.
"""

from __future__ import annotations

import re

from discovery.profile import MachineProfile
from orchestrator.logging_setup import get_logger
from recommendation.catalog import get_catalog
from recommendation.models import LLMBundle, RecommendationResult

log = get_logger("recommendation.engine")

_PARAM_SORT_KEY: dict[str, float] = {
    "1.3B": 1.3,
    "1.5B": 1.5,
    "3B": 3.0,
    "3.8B": 3.8,
    "6.7B": 6.7,
    "7B": 7.0,
    "13B": 13.0,
    "14B": 14.0,
    "15B": 15.0,
    "16B": 16.0,
    "22B": 22.0,
    "32B": 32.0,
    "33B": 33.0,
    "34B": 34.0,
}

_RAM_HEADROOM_GB = 2.0
_VRAM_HEADROOM_GB = 0.5


def _parse_param_count(raw: str) -> float:
    """Convert a human-friendly parameter count like '7B' to a float."""
    if raw in _PARAM_SORT_KEY:
        return _PARAM_SORT_KEY[raw]
    m = re.match(r"([\d.]+)\s*[Bb]", raw)
    return float(m.group(1)) if m else 0.0


class RecommendationEngine:
    """Selects the best local-LLM bundle for a given machine profile."""

    def recommend(
        self,
        profile: MachineProfile,
        intent: str = "coding",
    ) -> RecommendationResult:
        available_ram = profile.ram_info.total_gb - _RAM_HEADROOM_GB
        available_vram = self._total_vram(profile)
        available_disk = self._total_free_disk(profile)
        has_gpu = available_vram > 0

        tier = self._classify_tier(available_vram, available_ram)
        log.info(
            "Hardware tier=%s  ram=%.1fG  vram=%.1fG  disk=%.1fG  gpu=%s",
            tier, available_ram, available_vram, available_disk, has_gpu,
        )

        catalog = get_catalog()
        eligible = self._filter(catalog, available_ram, available_vram, available_disk, has_gpu)

        if not eligible:
            log.warning(
                "No model fits hardware constraints — falling back to smallest catalog entry"
            )
            eligible = sorted(catalog, key=lambda b: _parse_param_count(b.parameter_count))[:1]

        ranked = self._rank(eligible, intent)
        primary = ranked[0]

        lighter = self._pick_lighter(ranked, primary)
        heavier = self._pick_heavier(
            catalog, primary, available_ram, available_vram, available_disk, has_gpu, intent
        )

        explanations = self._build_explanations(primary, lighter, heavier, tier, intent)
        summary = self._machine_summary(profile, tier, has_gpu)

        log.info("Primary recommendation: %s", primary.model_id)
        return RecommendationResult(
            primary=primary,
            lighter=lighter,
            heavier=heavier,
            explanations=explanations,
            hardware_tier=tier,
            machine_summary=summary,
        )

    # ── internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _total_vram(profile: MachineProfile) -> float:
        if profile.gpu_info is None:
            return 0.0
        return profile.gpu_info.vram_gb

    @staticmethod
    def _total_free_disk(profile: MachineProfile) -> float:
        if not profile.storage_info.volumes:
            return 0.0
        return max(v.free_gb for v in profile.storage_info.volumes)

    @staticmethod
    def _classify_tier(vram: float, ram: float) -> str:
        if vram <= 0:
            if ram < 8:
                return "cpu-low"
            return "cpu-capable"
        if vram < 6:
            return "gpu-entry"
        if vram < 12:
            return "gpu-mid"
        if vram < 24:
            return "gpu-high"
        return "gpu-workstation"

    @staticmethod
    def _fits_hardware(
        bundle: LLMBundle,
        ram: float,
        vram: float,
        disk: float,
        has_gpu: bool,
    ) -> bool:
        if bundle.estimated_disk_gb > disk:
            return False
        if has_gpu and bundle.estimated_vram_gb is not None:
            needed_vram = bundle.estimated_vram_gb - _VRAM_HEADROOM_GB
            if needed_vram <= vram:
                return True
        return bundle.estimated_ram_gb <= ram

    def _filter(
        self,
        catalog: list[LLMBundle],
        ram: float,
        vram: float,
        disk: float,
        has_gpu: bool,
    ) -> list[LLMBundle]:
        return [b for b in catalog if self._fits_hardware(b, ram, vram, disk, has_gpu)]

    @staticmethod
    def _rank(bundles: list[LLMBundle], intent: str) -> list[LLMBundle]:
        def _score(b: LLMBundle) -> tuple[int, float, int]:
            spec_match = 1 if b.specialization == intent else 0
            size = _parse_param_count(b.parameter_count)
            ctx = b.context_size
            return (spec_match, size, ctx)

        return sorted(bundles, key=_score, reverse=True)

    def _pick_lighter(
        self,
        ranked: list[LLMBundle],
        primary: LLMBundle,
    ) -> LLMBundle | None:
        primary_size = _parse_param_count(primary.parameter_count)
        candidates = [
            b for b in ranked
            if _parse_param_count(b.parameter_count) < primary_size
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda b: _parse_param_count(b.parameter_count), reverse=True)
        return candidates[0]

    def _pick_heavier(
        self,
        catalog: list[LLMBundle],
        primary: LLMBundle,
        ram: float,
        vram: float,
        disk: float,
        has_gpu: bool,
        intent: str,
    ) -> LLMBundle | None:
        """Pick a model one step up that *might* still run (with possible swapping)."""
        primary_size = _parse_param_count(primary.parameter_count)
        relaxed_ram = ram * 1.3
        relaxed_vram = vram * 1.2

        candidates = [
            b for b in catalog
            if _parse_param_count(b.parameter_count) > primary_size
            and self._fits_hardware(b, relaxed_ram, relaxed_vram, disk, has_gpu)
        ]
        if not candidates:
            return None
        ranked = self._rank(candidates, intent)
        return ranked[0]

    @staticmethod
    def _build_explanations(
        primary: LLMBundle,
        lighter: LLMBundle | None,
        heavier: LLMBundle | None,
        tier: str,
        intent: str,
    ) -> dict[str, str]:
        explanations: dict[str, str] = {}

        explanations[primary.model_id] = (
            f"Best fit for your {tier} hardware with intent '{intent}'. "
            f"{primary.display_name} ({primary.parameter_count}, {primary.quantization}) "
            f"fits comfortably within your available memory and offers "
            f"a {primary.context_size:,}-token context window."
        )

        if lighter is not None:
            explanations[lighter.model_id] = (
                f"Lighter alternative: {lighter.display_name} uses less RAM/VRAM "
                f"(~{lighter.estimated_ram_gb:.1f} GB RAM) and responds faster, "
                f"ideal if you need snappy autocomplete or are running other heavy apps."
            )

        if heavier is not None:
            explanations[heavier.model_id] = (
                f"Stretch pick: {heavier.display_name} ({heavier.parameter_count}) "
                f"delivers higher quality but may cause memory pressure. "
                f"Consider it if you can close other applications."
            )

        return explanations

    @staticmethod
    def _machine_summary(profile: MachineProfile, tier: str, has_gpu: bool) -> str:
        gpu_desc = "no discrete GPU detected"
        if has_gpu and profile.gpu_info:
            gpu_desc = f"{profile.gpu_info.model} ({profile.gpu_info.vram_gb:.0f} GB VRAM)"

        free_disk = max((v.free_gb for v in profile.storage_info.volumes), default=0)
        return (
            f"{profile.ram_info.total_gb:.0f} GB RAM, {free_disk:.0f} GB free disk, "
            f"{gpu_desc} -- classified as '{tier}'."
        )

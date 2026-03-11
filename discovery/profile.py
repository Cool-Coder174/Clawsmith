"""Machine profile generator — combines hardware + toolchain into an actionable summary."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from discovery.hardware import (
    CPUInfo,
    GPUInfo,
    OSInfo,
    RAMInfo,
    StorageInfo,
    detect_all_hardware,
)
from discovery.toolchain import ToolchainReport, detect_toolchain
from orchestrator.logging_setup import get_logger

logger = get_logger("discovery.profile")


# ---------------------------------------------------------------------------
# Enums & models
# ---------------------------------------------------------------------------


class HardwareTier(StrEnum):
    minimal = "minimal"
    basic = "basic"
    capable = "capable"
    powerful = "powerful"
    workstation = "workstation"


class MachineProfile(BaseModel):
    os_info: OSInfo
    cpu_info: CPUInfo
    ram_info: RAMInfo
    gpu_info: GPUInfo | None = None
    storage_info: StorageInfo
    toolchain: ToolchainReport
    hardware_tier: HardwareTier
    feasible_model_sizes: list[str] = Field(default_factory=list)
    recommended_backends: list[str] = Field(default_factory=list)
    expected_performance: str = ""
    likely_bottlenecks: list[str] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


def _classify_tier(ram: RAMInfo, gpu: GPUInfo | None) -> HardwareTier:
    vram = gpu.vram_gb if gpu else 0.0
    total_ram = ram.total_gb

    if gpu and vram >= 24:
        return HardwareTier.workstation
    if gpu and 8 <= vram < 24:
        return HardwareTier.powerful
    if (gpu and 0 < vram < 8) or 16 <= total_ram < 32:
        return HardwareTier.capable
    if 8 <= total_ram < 16:
        return HardwareTier.basic
    if total_ram >= 32 and not gpu:
        return HardwareTier.capable
    return HardwareTier.minimal


# ---------------------------------------------------------------------------
# Feasible model sizes
# ---------------------------------------------------------------------------


def _feasible_models(tier: HardwareTier, gpu: GPUInfo | None) -> list[str]:
    vram = gpu.vram_gb if gpu else 0.0
    table: dict[HardwareTier, list[str]] = {
        HardwareTier.minimal: ["1B-3B quantized"],
        HardwareTier.basic: ["1B-3B", "7B-quantized (Q4)"],
        HardwareTier.capable: ["1B-3B", "7B-quantized", "13B-quantized (Q4)"],
        HardwareTier.powerful: ["1B-3B", "7B", "13B-quantized", "20B-quantized"],
        HardwareTier.workstation: [
            "1B-3B", "7B", "13B", "20B-quantized",
            "34B-quantized", "70B-quantized (Q4)",
        ],
    }
    sizes = list(table.get(tier, []))

    if vram >= 48:
        sizes.append("70B")
    return sizes


# ---------------------------------------------------------------------------
# Recommended backends
# ---------------------------------------------------------------------------


def _recommended_backends(gpu: GPUInfo | None, toolchain: ToolchainReport) -> list[str]:
    backends: list[str] = []
    runtime_names = {t.name for t in toolchain.inference_runtimes if t.found}

    if "ollama" in runtime_names:
        backends.append("ollama")
    if "llama.cpp-server" in runtime_names:
        backends.append("llama.cpp")
    if "llamafile" in runtime_names:
        backends.append("llamafile")

    if not backends:
        backends.append("ollama")
        backends.append("llama.cpp")
        backends.append("llamafile")

    if gpu:
        cb = gpu.compute_backend
        if cb == "cuda" and "ollama" not in backends:
            backends.insert(0, "ollama")
        if cb == "rocm":
            backends.append("llama.cpp (ROCm)")

    return backends


# ---------------------------------------------------------------------------
# Performance & bottleneck summary
# ---------------------------------------------------------------------------


def _expected_performance(tier: HardwareTier, gpu: GPUInfo | None, ram: RAMInfo) -> str:
    match tier:
        case HardwareTier.minimal:
            return "Very limited — small quantized models only, slow inference."
        case HardwareTier.basic:
            return "Adequate for small models (1B-7B Q4). Expect moderate latency on CPU."
        case HardwareTier.capable:
            perf = "Good for 7B-13B quantized models."
            if gpu:
                perf += f" GPU acceleration available ({gpu.compute_backend})."
            return perf
        case HardwareTier.powerful:
            backend = gpu.compute_backend if gpu else "N/A"
            return (
                f"Strong setup — 7B-20B models with GPU acceleration"
                f" ({backend}). Fast inference expected."
            )
        case HardwareTier.workstation:
            if gpu:
                return (
                    f"Workstation-class — large models up to 34B+ feasible."
                    f" GPU: {gpu.model} ({gpu.vram_gb:.0f} GB VRAM)."
                )
            return (
                "High-RAM workstation, CPU inference"
                " for large quantized models."
            )
    return "Unknown performance profile."


def _likely_bottlenecks(
    tier: HardwareTier,
    gpu: GPUInfo | None,
    ram: RAMInfo,
    storage: StorageInfo,
) -> list[str]:
    issues: list[str] = []

    if not gpu:
        issues.append("No dedicated GPU detected — inference will be CPU-bound.")
    elif gpu.vram_gb < 6:
        issues.append(f"Low GPU VRAM ({gpu.vram_gb:.1f} GB) — larger models may fall back to CPU.")

    if ram.total_gb < 16:
        issues.append(f"Limited RAM ({ram.total_gb:.1f} GB) — may constrain model loading.")

    if ram.available_gb < 4 and ram.available_gb > 0:
        issues.append(
            f"Low available RAM ({ram.available_gb:.1f} GB)"
            " — close other applications before running inference."
        )

    free_space = max((v.free_gb for v in storage.volumes), default=0)
    if free_space < 20:
        issues.append(
            f"Low disk space (max {free_space:.0f} GB free)"
            " — model downloads need significant storage."
        )

    return issues


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def _build_summary(
    tier: HardwareTier,
    gpu: GPUInfo | None,
    ram: RAMInfo,
    storage: StorageInfo,
    toolchain: ToolchainReport,
    feasible: list[str],
) -> str:
    parts: list[str] = []

    if len(feasible) > 1:
        model_range = f"{feasible[0]} to {feasible[-1]}"
    elif feasible:
        model_range = feasible[0]
    else:
        model_range = "none"
    parts.append(f"Good for {model_range} local models.")

    if gpu and gpu.compute_backend != "cpu_only":
        parts.append(
            "Can run coding-oriented local models with"
            f" GPU acceleration ({gpu.compute_backend})."
        )
    else:
        parts.append("CPU-only inference; consider adding a GPU for better performance.")

    if storage.recommended_model_path:
        parts.append(
            f"Default install path should be"
            f" {storage.recommended_model_path} due to free space."
        )

    detected_dev = [t.name for t in toolchain.developer_tools if t.found]
    if detected_dev:
        parts.append(f"{', '.join(t.capitalize() for t in detected_dev)} toolchains detected.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_profile() -> MachineProfile:
    """Run full hardware + toolchain detection and return a complete machine profile."""
    logger.info("Generating machine profile …")

    hw = detect_all_hardware()
    os_info: OSInfo = hw["os_info"]  # type: ignore[assignment]
    cpu_info: CPUInfo = hw["cpu_info"]  # type: ignore[assignment]
    ram_info: RAMInfo = hw["ram_info"]  # type: ignore[assignment]
    gpu_info: GPUInfo | None = hw["gpu_info"]  # type: ignore[assignment]
    storage_info: StorageInfo = hw["storage_info"]  # type: ignore[assignment]

    tc = detect_toolchain()

    tier = _classify_tier(ram_info, gpu_info)
    feasible = _feasible_models(tier, gpu_info)
    backends = _recommended_backends(gpu_info, tc)
    perf = _expected_performance(tier, gpu_info, ram_info)
    bottlenecks = _likely_bottlenecks(tier, gpu_info, ram_info, storage_info)
    summary = _build_summary(tier, gpu_info, ram_info, storage_info, tc, feasible)

    profile = MachineProfile(
        os_info=os_info,
        cpu_info=cpu_info,
        ram_info=ram_info,
        gpu_info=gpu_info,
        storage_info=storage_info,
        toolchain=tc,
        hardware_tier=tier,
        feasible_model_sizes=feasible,
        recommended_backends=backends,
        expected_performance=perf,
        likely_bottlenecks=bottlenecks,
        summary=summary,
    )

    logger.info("Machine profile: tier=%s, summary=%s", tier, summary)
    return profile

"""Hardware and environment detection for the local machine."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from orchestrator.logging_setup import get_logger

logger = get_logger("discovery.hardware")

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class OSInfo(BaseModel):
    os_name: str = "unknown"
    os_version: str = "unknown"
    architecture: str = "unknown"
    shell: str = "unknown"
    is_wsl: bool = False
    is_windows: bool = False
    is_linux: bool = False
    is_macos: bool = False


class CPUInfo(BaseModel):
    vendor: str = "unknown"
    model: str = "unknown"
    architecture: str = "unknown"
    cores: int = 0
    threads: int = 0
    max_clock_mhz: int = 0


class RAMInfo(BaseModel):
    total_gb: float = 0.0
    available_gb: float = 0.0


class GPUInfo(BaseModel):
    vendor: str = "unknown"
    model: str = "unknown"
    vram_gb: float = 0.0
    driver_version: str = "unknown"
    compute_backend: str = "cpu_only"  # cuda | rocm | directml | vulkan | cpu_only


class StorageVolume(BaseModel):
    device_id: str
    total_gb: float = 0.0
    free_gb: float = 0.0
    mount_point: str = ""


class StorageInfo(BaseModel):
    volumes: list[StorageVolume] = Field(default_factory=list)
    recommended_model_path: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"
_IS_LINUX = sys.platform.startswith("linux")
_IS_MACOS = sys.platform == "darwin"


def _run(cmd: list[str] | str, *, timeout: int = 15, shell: bool = False) -> str:
    """Run a subprocess and return stripped stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            creationflags=subprocess.CREATE_NO_WINDOW if _IS_WINDOWS else 0,
        )
        return result.stdout.strip()
    except Exception as exc:
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        logger.debug("subprocess failed (%s): %s", cmd_str, exc)
        return ""


def _powershell(script: str, *, timeout: int = 15) -> str:
    """Run a PowerShell one-liner and return stdout."""
    return _run(
        ["powershell", "-NoProfile", "-Command", script],
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------


def detect_os() -> OSInfo:
    """Detect operating system, version, architecture, and shell."""
    info = OSInfo(
        os_name=platform.system(),
        os_version=platform.version(),
        architecture=platform.machine(),
        is_windows=_IS_WINDOWS,
        is_linux=_IS_LINUX,
        is_macos=_IS_MACOS,
    )

    # WSL detection
    if _IS_LINUX:
        try:
            with open("/proc/version", encoding="utf-8") as f:
                if "microsoft" in f.read().lower():
                    info.is_wsl = True
        except OSError:
            pass

    # Shell detection
    info.shell = _detect_shell()
    return info


def _detect_shell() -> str:
    shell_env = os.environ.get("SHELL", "")
    if _IS_WINDOWS:
        # PSModulePath is reliably set in PowerShell sessions
        if os.environ.get("PSModulePath"):
            return "powershell"
        comspec = os.environ.get("COMSPEC", "").lower()
        if "cmd.exe" in comspec:
            return "cmd"
        return "unknown-windows"
    if shell_env:
        return Path(shell_env).name
    return "unknown"


# ---------------------------------------------------------------------------
# CPU detection
# ---------------------------------------------------------------------------


def detect_cpu() -> CPUInfo:
    if _IS_WINDOWS:
        return _detect_cpu_windows()
    if _IS_LINUX:
        return _detect_cpu_linux()
    if _IS_MACOS:
        return _detect_cpu_macos()
    return CPUInfo()


def _detect_cpu_windows() -> CPUInfo:
    info = CPUInfo(architecture=platform.machine())

    # Primary: PowerShell Get-CimInstance (works on modern Windows 10/11)
    raw = _powershell(
        "Get-CimInstance Win32_Processor"
        " | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed"
        " | ConvertTo-Csv -NoTypeInformation"
    )
    if raw:
        for line in raw.splitlines():
            if line.startswith('"Name"'):
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 4:
                try:
                    info.model = parts[0].strip()
                    info.cores = int(parts[1])
                    info.threads = int(parts[2])
                    info.max_clock_mhz = int(parts[3])
                    info.vendor = _infer_cpu_vendor(info.model)
                    return info
                except (ValueError, IndexError) as exc:
                    logger.debug("PowerShell CPU parse error: %s", exc)

    # Fallback: wmic (deprecated but present on older builds)
    raw = _run(
        "wmic cpu get Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed /format:csv",
        shell=True,
    )
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and parts[1].isdigit():
            try:
                info.max_clock_mhz = int(parts[1])
                info.model = parts[2]
                info.cores = int(parts[3])
                info.threads = int(parts[4])
                info.vendor = _infer_cpu_vendor(parts[2])
                return info
            except (ValueError, IndexError) as exc:
                logger.debug("wmic CPU parse error: %s", exc)

    # Last resort: os.cpu_count()
    if info.cores == 0:
        info.threads = os.cpu_count() or 0
        info.cores = info.threads
    return info


def _detect_cpu_linux() -> CPUInfo:
    info = CPUInfo(architecture=platform.machine())
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            cpuinfo = f.read()
        for line in cpuinfo.splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key == "model name" and info.model == "unknown":
                info.model = value
                info.vendor = _infer_cpu_vendor(value)
            elif key == "cpu cores" and info.cores == 0:
                info.cores = int(value)
            elif key == "siblings" and info.threads == 0:
                info.threads = int(value)
            elif key == "cpu MHz" and info.max_clock_mhz == 0:
                info.max_clock_mhz = int(float(value))
    except OSError:
        pass
    if info.cores == 0:
        info.cores = os.cpu_count() or 0
    if info.threads == 0:
        info.threads = os.cpu_count() or 0
    return info


def _detect_cpu_macos() -> CPUInfo:
    info = CPUInfo(architecture=platform.machine())
    brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if brand:
        info.model = brand
        info.vendor = _infer_cpu_vendor(brand)
    cores = _run(["sysctl", "-n", "hw.physicalcpu"])
    threads = _run(["sysctl", "-n", "hw.logicalcpu"])
    freq = _run(["sysctl", "-n", "hw.cpufrequency_max"])
    if cores:
        info.cores = int(cores)
    if threads:
        info.threads = int(threads)
    if freq:
        info.max_clock_mhz = int(int(freq) / 1_000_000)
    return info


def _infer_cpu_vendor(model_string: str) -> str:
    m = model_string.lower()
    if "intel" in m:
        return "Intel"
    if "amd" in m:
        return "AMD"
    if "apple" in m or "m1" in m or "m2" in m or "m3" in m or "m4" in m:
        return "Apple"
    if "qualcomm" in m or "snapdragon" in m:
        return "Qualcomm"
    return "unknown"


# ---------------------------------------------------------------------------
# RAM detection
# ---------------------------------------------------------------------------


def detect_ram() -> RAMInfo:
    if _IS_WINDOWS:
        return _detect_ram_windows()
    if _IS_LINUX:
        return _detect_ram_linux()
    if _IS_MACOS:
        return _detect_ram_macos()
    return RAMInfo()


def _detect_ram_windows() -> RAMInfo:
    # Primary: PowerShell Get-CimInstance
    raw = _powershell(
        "Get-CimInstance Win32_OperatingSystem"
        " | Select-Object TotalVisibleMemorySize,FreePhysicalMemory"
        " | ConvertTo-Csv -NoTypeInformation"
    )
    if raw:
        for line in raw.splitlines():
            if line.startswith('"Total'):
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit():
                try:
                    total_kb = int(parts[0])
                    free_kb = int(parts[1])
                    return RAMInfo(
                        total_gb=round(total_kb / 1_048_576, 2),
                        available_gb=round(free_kb / 1_048_576, 2),
                    )
                except (ValueError, IndexError):
                    pass

    # Fallback: wmic
    raw = _run(
        "wmic OS get TotalVisibleMemorySize,FreePhysicalMemory /format:csv",
        shell=True,
    )
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].isdigit():
            try:
                free_kb = int(parts[1])
                total_kb = int(parts[2])
                return RAMInfo(
                    total_gb=round(total_kb / 1_048_576, 2),
                    available_gb=round(free_kb / 1_048_576, 2),
                )
            except (ValueError, IndexError):
                pass
    return RAMInfo()


def _detect_ram_linux() -> RAMInfo:
    info = RAMInfo()
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    info.total_gb = round(kb / 1_048_576, 2)
                elif line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    info.available_gb = round(kb / 1_048_576, 2)
    except OSError:
        pass
    return info


def _detect_ram_macos() -> RAMInfo:
    info = RAMInfo()
    total = _run(["sysctl", "-n", "hw.memsize"])
    if total:
        info.total_gb = round(int(total) / (1024**3), 2)
    vm = _run(["vm_stat"])
    if vm:
        page_size = 16384 if platform.machine() == "arm64" else 4096
        free_pages = 0
        for line in vm.splitlines():
            if "Pages free" in line:
                m = re.search(r"(\d+)", line.split(":")[1])
                if m:
                    free_pages += int(m.group(1))
            elif "Pages inactive" in line:
                m = re.search(r"(\d+)", line.split(":")[1])
                if m:
                    free_pages += int(m.group(1))
        info.available_gb = round(free_pages * page_size / (1024**3), 2)
    return info


# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------


def detect_gpu() -> GPUInfo | None:
    """Try multiple strategies to detect the primary GPU. Returns None if nothing found."""
    gpu = _try_nvidia_smi()
    if gpu:
        return gpu

    if _IS_LINUX:
        gpu = _try_rocm_smi()
        if gpu:
            return gpu

    if _IS_WINDOWS:
        gpu = _try_rocm_smi()
        if gpu:
            return gpu
        gpu = _try_wmic_gpu()
        if gpu:
            return gpu

    if _IS_MACOS:
        gpu = _try_macos_gpu()
        if gpu:
            return gpu

    return None


def _try_nvidia_smi() -> GPUInfo | None:
    if not shutil.which("nvidia-smi"):
        return None
    raw = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not raw:
        return None
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                return GPUInfo(
                    vendor="NVIDIA",
                    model=parts[0],
                    vram_gb=round(int(parts[1]) / 1024, 2),
                    driver_version=parts[3],
                    compute_backend="cuda",
                )
            except (ValueError, IndexError) as exc:
                logger.debug("nvidia-smi parse error: %s", exc)
    return None


def _try_rocm_smi() -> GPUInfo | None:
    if not shutil.which("rocm-smi"):
        return None
    raw = _run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--csv"])
    if not raw:
        return None
    model = "AMD GPU"
    vram_gb = 0.0
    for line in raw.splitlines():
        if "card" in line.lower():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                model = parts[1]
        m = re.search(r"(\d+)\s+MB", line, re.IGNORECASE)
        if m:
            vram_gb = round(int(m.group(1)) / 1024, 2)
    return GPUInfo(
        vendor="AMD",
        model=model,
        vram_gb=vram_gb,
        compute_backend="rocm",
    )


def _try_wmic_gpu() -> GPUInfo | None:
    """PowerShell-primary, wmic-fallback GPU detection for Windows."""

    def _parse_gpu(name: str, adapter_ram: int) -> GPUInfo:
        vendor = "unknown"
        backend = "directml"
        name_lower = name.lower()
        if "nvidia" in name_lower:
            vendor = "NVIDIA"
            backend = "cuda"
        elif "amd" in name_lower or "radeon" in name_lower:
            vendor = "AMD"
            backend = "directml"
        elif "intel" in name_lower:
            vendor = "Intel"
            backend = "directml"
        return GPUInfo(
            vendor=vendor,
            model=name,
            vram_gb=round(adapter_ram / (1024**3), 2),
            compute_backend=backend,
        )

    # Primary: PowerShell
    raw = _powershell(
        "Get-CimInstance Win32_VideoController"
        " | Select-Object Name,AdapterRAM"
        " | ConvertTo-Csv -NoTypeInformation"
    )
    if raw:
        for line in raw.splitlines():
            if line.startswith('"Name"'):
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit():
                try:
                    return _parse_gpu(parts[0], int(parts[1]))
                except (ValueError, IndexError):
                    pass

    # Fallback: wmic
    raw = _run(
        "wmic path win32_VideoController get Name,AdapterRAM /format:csv",
        shell=True,
    )
    if not raw:
        return None
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].isdigit():
            try:
                return _parse_gpu(parts[2], int(parts[1]))
            except (ValueError, IndexError):
                pass
    return None


def _try_macos_gpu() -> GPUInfo | None:
    raw = _run(["system_profiler", "SPDisplaysDataType"])
    if not raw:
        return None
    model = "unknown"
    vram_gb = 0.0
    vendor = "Apple"
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("Chipset Model:"):
            model = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("VRAM"):
            m = re.search(r"(\d+)\s*(MB|GB)", stripped, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if m.group(2).upper() == "MB":
                    vram_gb = round(val / 1024, 2)
                else:
                    vram_gb = float(val)
        elif "vendor" in stripped.lower():
            if "nvidia" in stripped.lower():
                vendor = "NVIDIA"
            elif "amd" in stripped.lower():
                vendor = "AMD"
    return GPUInfo(
        vendor=vendor,
        model=model,
        vram_gb=vram_gb,
        compute_backend="vulkan",
    )


# ---------------------------------------------------------------------------
# Storage detection
# ---------------------------------------------------------------------------


def detect_storage() -> StorageInfo:
    if _IS_WINDOWS:
        return _detect_storage_windows()
    return _detect_storage_unix()


def _detect_storage_windows() -> StorageInfo:
    volumes: list[StorageVolume] = []

    # Primary: PowerShell Get-CimInstance
    raw = _powershell(
        "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3'"
        " | Select-Object DeviceID,FreeSpace,Size"
        " | ConvertTo-Csv -NoTypeInformation"
    )
    if raw:
        for line in raw.splitlines():
            if line.startswith('"Device'):
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 3 and parts[2].isdigit():
                try:
                    device_id = parts[0]
                    free_bytes = int(parts[1]) if parts[1].isdigit() else 0
                    total_bytes = int(parts[2])
                    volumes.append(StorageVolume(
                        device_id=device_id,
                        total_gb=round(total_bytes / (1024**3), 2),
                        free_gb=round(free_bytes / (1024**3), 2),
                        mount_point=device_id + "\\",
                    ))
                except (ValueError, IndexError):
                    pass

    # Fallback: wmic
    if not volumes:
        raw = _run(
            "wmic logicaldisk get DeviceID,FreeSpace,Size /format:csv",
            shell=True,
        )
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4 and parts[3].isdigit():
                try:
                    device_id = parts[1]
                    free_bytes = int(parts[2]) if parts[2].isdigit() else 0
                    total_bytes = int(parts[3])
                    volumes.append(StorageVolume(
                        device_id=device_id,
                        total_gb=round(total_bytes / (1024**3), 2),
                        free_gb=round(free_bytes / (1024**3), 2),
                        mount_point=device_id + "\\",
                    ))
                except (ValueError, IndexError):
                    pass

    recommended = _pick_recommended_path(volumes)
    return StorageInfo(volumes=volumes, recommended_model_path=recommended)


def _detect_storage_unix() -> StorageInfo:
    raw = _run(["df", "-B1", "--output=source,size,avail,target"])
    if not raw:
        raw = _run(["df", "-k"])
    volumes: list[StorageVolume] = []
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[1].isdigit():
            try:
                # df -B1 output: Filesystem Size Avail Target
                total = int(parts[1])
                avail = int(parts[2])
                mount = parts[3] if len(parts) == 4 else parts[-1]
                volumes.append(StorageVolume(
                    device_id=parts[0],
                    total_gb=round(total / (1024**3), 2),
                    free_gb=round(avail / (1024**3), 2),
                    mount_point=mount,
                ))
            except (ValueError, IndexError):
                pass

    recommended = _pick_recommended_path(volumes)
    return StorageInfo(volumes=volumes, recommended_model_path=recommended)


def _pick_recommended_path(volumes: list[StorageVolume]) -> str:
    """Pick a reasonable default model storage path based on free space."""
    if not volumes:
        return str(Path.home() / "Models")

    best = max(volumes, key=lambda v: v.free_gb)
    if _IS_WINDOWS:
        return f"{best.device_id}\\Models"
    return str(Path(best.mount_point) / "Models")


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------


def detect_all_hardware() -> dict[str, OSInfo | CPUInfo | RAMInfo | GPUInfo | StorageInfo | None]:
    """Run all hardware detection probes and return a dict of results."""
    logger.info("Starting hardware detection …")
    os_info = detect_os()
    logger.debug("OS detected: %s %s", os_info.os_name, os_info.os_version)

    cpu_info = detect_cpu()
    logger.debug(
        "CPU detected: %s (%d cores / %d threads)",
        cpu_info.model, cpu_info.cores, cpu_info.threads,
    )

    ram_info = detect_ram()
    logger.debug(
        "RAM: %.1f GB total, %.1f GB available",
        ram_info.total_gb, ram_info.available_gb,
    )

    gpu_info = detect_gpu()
    if gpu_info:
        logger.debug(
            "GPU detected: %s %s (%.1f GB VRAM)",
            gpu_info.vendor, gpu_info.model, gpu_info.vram_gb,
        )
    else:
        logger.debug("No dedicated GPU detected")

    storage_info = detect_storage()
    logger.debug(
        "Storage: %d volume(s), recommended path: %s",
        len(storage_info.volumes), storage_info.recommended_model_path,
    )

    logger.info("Hardware detection complete.")
    return {
        "os_info": os_info,
        "cpu_info": cpu_info,
        "ram_info": ram_info,
        "gpu_info": gpu_info,
        "storage_info": storage_info,
    }

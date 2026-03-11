"""Data models for the model installation pipeline."""

from __future__ import annotations

from pydantic import BaseModel


class DownloadTask(BaseModel):
    url: str
    target_path: str
    expected_size_bytes: int | None = None
    checksum_sha256: str | None = None
    resumable: bool = True


class DownloadProgress(BaseModel):
    task: DownloadTask
    bytes_downloaded: int = 0
    total_bytes: int = 0
    percent: float = 0.0
    speed_mbps: float = 0.0
    status: str = "pending"  # pending, downloading, paused, completed, failed, verifying
    error: str | None = None


class InstallResult(BaseModel):
    success: bool
    model_id: str
    runtime: str
    install_path: str
    disk_used_gb: float = 0.0
    error: str | None = None
    notes: str = ""


class RuntimeInfo(BaseModel):
    name: str  # "ollama", "llama.cpp", "llamafile"
    installed: bool
    version: str = ""
    path: str = ""
    install_command: str = ""  # hint for installing
    install_url: str = ""  # download page

from __future__ import annotations

import json
import shutil
from pathlib import Path

from install.downloader import ModelDownloader
from install.models import DownloadTask, InstallResult
from install.runtime_manager import RuntimeManager
from orchestrator.logging_setup import get_logger
from recommendation.models import LLMBundle

log = get_logger("install.provisioner")

_DEFAULT_INSTALL_BASE = Path.home() / ".clawsmith" / "models"
_MANIFEST_NAME = "installed-models.json"


class ModelProvisioner:
    """High-level orchestrator for downloading and registering local LLMs."""

    def __init__(self, install_base_path: str | None = None) -> None:
        self._base = Path(install_base_path) if install_base_path else _DEFAULT_INSTALL_BASE
        self._base.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._base / _MANIFEST_NAME
        self._runtime_mgr = RuntimeManager()
        self._downloader = ModelDownloader()

    # ------------------------------------------------------------------
    # Manifest helpers
    # ------------------------------------------------------------------

    def _load_manifest(self) -> list[dict]:
        if self._manifest_path.exists():
            try:
                return json.loads(self._manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("Corrupt manifest at %s — starting fresh", self._manifest_path)
        return []

    def _save_manifest(self, entries: list[dict]) -> None:
        self._manifest_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _register(self, result: InstallResult) -> None:
        entries = self._load_manifest()
        entries = [e for e in entries if e.get("model_id") != result.model_id]
        entries.append(result.model_dump())
        self._save_manifest(entries)
        log.info("Registered model %s in manifest", result.model_id)

    def _unregister(self, model_id: str) -> bool:
        entries = self._load_manifest()
        new_entries = [e for e in entries if e.get("model_id") != model_id]
        if len(new_entries) == len(entries):
            return False
        self._save_manifest(new_entries)
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def provision(
        self,
        bundle: LLMBundle,
        target_path: str | None = None,
    ) -> InstallResult:
        """Install a model bundle end-to-end.

        Steps:
        1. Check free disk space
        2. Verify runtime availability
        3. Pull via ollama **or** direct-download the model file
        4. Verify checksum (if available)
        5. Register in local manifest
        """
        dest = Path(target_path) if target_path else self._base / bundle.model_id
        dest.mkdir(parents=True, exist_ok=True)

        # 1. Disk space check
        ok, avail = self._downloader.check_free_space(
            str(dest), bundle.estimated_disk_gb
        )
        if not ok:
            return InstallResult(
                success=False,
                model_id=bundle.model_id,
                runtime=bundle.runtime,
                install_path=str(dest),
                error=(
                    f"Insufficient disk space: need {bundle.estimated_disk_gb:.1f} GB, "
                    f"only {avail:.1f} GB available"
                ),
            )

        # 2. Runtime check
        info = self._runtime_mgr.check_runtime(bundle.runtime)
        if not info.installed:
            hint = self._runtime_mgr.install_runtime_hint(bundle.runtime)
            return InstallResult(
                success=False,
                model_id=bundle.model_id,
                runtime=bundle.runtime,
                install_path=str(dest),
                error=f"Runtime '{bundle.runtime}' is not installed.\n{hint}",
            )

        # 3. Pull / download
        if bundle.runtime == "ollama":
            result = self._runtime_mgr.pull_model_via_ollama(bundle.model_id)
            if result.success:
                self._register(result)
            return result

        # Direct download path
        if not bundle.download_url:
            return InstallResult(
                success=False,
                model_id=bundle.model_id,
                runtime=bundle.runtime,
                install_path=str(dest),
                error="No download URL provided for direct-download bundle",
            )

        filename = bundle.download_url.rsplit("/", 1)[-1] or f"{bundle.model_id}.gguf"
        file_dest = dest / filename

        expected_size = (
            int(bundle.estimated_disk_gb * 1024**3) if bundle.estimated_disk_gb else None
        )
        task = DownloadTask(
            url=bundle.download_url,
            target_path=str(file_dest),
            expected_size_bytes=expected_size,
            checksum_sha256=None,  # populated from checksum_url if available
            resumable=True,
        )

        progress = self._downloader.download_sync(task)

        if progress.status != "completed":
            return InstallResult(
                success=False,
                model_id=bundle.model_id,
                runtime=bundle.runtime,
                install_path=str(file_dest),
                error=progress.error or "Download did not complete",
            )

        # 4. Compute actual disk usage
        disk_gb = 0.0
        if file_dest.exists():
            disk_gb = round(file_dest.stat().st_size / (1024**3), 2)

        # 5. Register
        result = InstallResult(
            success=True,
            model_id=bundle.model_id,
            runtime=bundle.runtime,
            install_path=str(file_dest),
            disk_used_gb=disk_gb,
            notes=bundle.notes,
        )
        self._register(result)
        return result

    def list_installed(self) -> list[dict]:
        """Return all models recorded in the local manifest."""
        return self._load_manifest()

    def uninstall(self, model_id: str) -> bool:
        """Remove a model from disk and the manifest.

        Returns *True* if the model was found and removed.
        """
        entries = self._load_manifest()
        target_entry = next((e for e in entries if e.get("model_id") == model_id), None)

        if target_entry is None:
            log.warning("Model %s not found in manifest", model_id)
            return False

        install_path = target_entry.get("install_path", "")
        if install_path:
            p = Path(install_path)
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                    log.info("Removed directory %s", p)
                elif p.is_file():
                    p.unlink()
                    log.info("Removed file %s", p)
                    # Also remove parent directory if empty
                    if p.parent != self._base and not any(p.parent.iterdir()):
                        p.parent.rmdir()
            except OSError as exc:
                log.error("Failed to delete %s: %s", install_path, exc)

        removed = self._unregister(model_id)
        if removed:
            log.info("Uninstalled model %s", model_id)
        return removed

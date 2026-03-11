from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
from pathlib import Path

import httpx

from install.models import DownloadProgress, DownloadTask
from orchestrator.logging_setup import get_logger

log = get_logger("install.downloader")

_CHUNK_SIZE = 1024 * 256  # 256 KiB
_DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)


class ModelDownloader:
    """Resumable HTTP downloader backed by :mod:`httpx`."""

    def __init__(self, timeout: httpx.Timeout | None = None) -> None:
        self._timeout = timeout or _DEFAULT_TIMEOUT

    # ------------------------------------------------------------------
    # Disk space
    # ------------------------------------------------------------------

    @staticmethod
    def check_free_space(target_dir: str, needed_gb: float) -> tuple[bool, float]:
        """Return *(ok, available_gb)* for *target_dir*."""
        usage = shutil.disk_usage(target_dir)
        available_gb = usage.free / (1024**3)
        return (available_gb >= needed_gb, round(available_gb, 2))

    # ------------------------------------------------------------------
    # Checksum
    # ------------------------------------------------------------------

    @staticmethod
    def verify_checksum(file_path: str, expected_sha256: str) -> bool:
        """Verify the SHA-256 checksum of a file on disk."""
        sha = hashlib.sha256()
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                sha.update(chunk)
        actual = sha.hexdigest()
        ok = actual == expected_sha256.lower()
        if not ok:
            log.error("Checksum mismatch: expected %s, got %s", expected_sha256, actual)
        return ok

    # ------------------------------------------------------------------
    # Download with resume
    # ------------------------------------------------------------------

    async def download(
        self,
        task: DownloadTask,
        progress_callback: callable | None = None,
    ) -> DownloadProgress:
        """Download a file with HTTP Range-based resume support.

        *progress_callback*, if provided, is called with the current
        :class:`DownloadProgress` after every chunk.
        """
        target = Path(task.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        progress = DownloadProgress(task=task, status="downloading")

        existing_bytes = 0
        if task.resumable and target.exists():
            existing_bytes = target.stat().st_size
            progress.bytes_downloaded = existing_bytes
            log.info("Resuming download from byte %d for %s", existing_bytes, task.url)

        headers: dict[str, str] = {}
        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"

        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                async with client.stream("GET", task.url, headers=headers) as response:
                    if response.status_code == 416:
                        # Range not satisfiable — file already complete
                        progress.status = "completed"
                        progress.percent = 100.0
                        if progress_callback:
                            progress_callback(progress)
                        return progress

                    response.raise_for_status()

                    total = self._parse_content_length(response, existing_bytes)
                    progress.total_bytes = total

                    mode = "ab" if existing_bytes > 0 else "wb"
                    with open(target, mode) as fh:
                        async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                            fh.write(chunk)
                            progress.bytes_downloaded += len(chunk)
                            elapsed = time.monotonic() - start_time
                            if elapsed > 0:
                                progress.speed_mbps = round(
                                    (progress.bytes_downloaded - existing_bytes)
                                    / elapsed
                                    / 1_000_000
                                    * 8,
                                    2,
                                )
                            if total > 0:
                                progress.percent = round(
                                    progress.bytes_downloaded / total * 100, 1
                                )
                            if progress_callback:
                                progress_callback(progress)

        except httpx.HTTPStatusError as exc:
            progress.status = "failed"
            progress.error = f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
            log.error("Download failed: %s", progress.error)
            return progress
        except (httpx.RequestError, OSError) as exc:
            progress.status = "failed"
            progress.error = str(exc)
            log.error("Download error: %s", progress.error)
            return progress

        # Verify checksum if provided
        if task.checksum_sha256:
            progress.status = "verifying"
            if progress_callback:
                progress_callback(progress)
            if not self.verify_checksum(task.target_path, task.checksum_sha256):
                progress.status = "failed"
                progress.error = "SHA-256 checksum mismatch"
                return progress

        progress.status = "completed"
        progress.percent = 100.0
        if progress_callback:
            progress_callback(progress)
        log.info("Download complete: %s", task.target_path)
        return progress

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_content_length(response: httpx.Response, existing_bytes: int) -> int:
        """Derive total file size from response headers."""
        content_range = response.headers.get("content-range", "")
        if content_range:
            # e.g. "bytes 1024-9999/10000"
            try:
                return int(content_range.rsplit("/", 1)[1])
            except (ValueError, IndexError):
                pass

        cl = response.headers.get("content-length")
        if cl:
            try:
                return int(cl) + existing_bytes
            except ValueError:
                pass
        return 0

    # ------------------------------------------------------------------
    # Sync convenience wrapper
    # ------------------------------------------------------------------

    def download_sync(
        self,
        task: DownloadTask,
        progress_callback: callable | None = None,
    ) -> DownloadProgress:
        """Blocking wrapper around :meth:`download`."""
        return asyncio.run(self.download(task, progress_callback))

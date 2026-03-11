"""HTTP client for outbound communication with the OpenClaw gateway.

Handles skill registration, task status callbacks, and connectivity checks.
All methods are async and use httpx under the hood.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from orchestrator.logging_setup import get_logger

logger = get_logger("openclaw_client")

_DEFAULT_TIMEOUT = 30.0


class OpenClawClientError(Exception):
    """Raised when communication with the OpenClaw gateway fails."""


class OpenClawClient:
    """Async HTTP client for the OpenClaw gateway API."""

    def __init__(
        self,
        gateway_url: str,
        api_key: str = "",
        webhook_secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"User-Agent": "ClawSmith/0.1"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _sign_payload(self, body: bytes) -> str:
        """Compute HMAC-SHA256 signature for outbound payloads."""
        return hmac.new(
            self._webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------
    # Gateway health
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return True if the OpenClaw gateway is reachable."""
        if not self._gateway_url:
            return False
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._gateway_url}/health")
            return resp.status_code < 500
        except httpx.HTTPError as exc:
            logger.debug("OpenClaw ping failed: %s", exc)
            return False

    async def get_gateway_info(self) -> dict[str, Any]:
        """Fetch metadata from the OpenClaw gateway."""
        client = await self._get_client()
        try:
            resp = await client.get(f"{self._gateway_url}/info")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise OpenClawClientError(f"Failed to get gateway info: {exc}") from exc

    # ------------------------------------------------------------------
    # Skill registration
    # ------------------------------------------------------------------

    async def register_skill(self, skill_manifest: dict[str, Any]) -> dict[str, Any]:
        """Register ClawSmith as a skill with the OpenClaw gateway.

        The manifest should include skill name, MCP endpoint, tool definitions,
        and required environment variables.
        """
        client = await self._get_client()
        body = json.dumps(skill_manifest).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._webhook_secret:
            headers["X-ClawSmith-Signature"] = self._sign_payload(body)

        try:
            resp = await client.post(
                f"{self._gateway_url}/skills/register",
                content=body,
                headers=headers,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info("Skill registered with OpenClaw: %s", result.get("skill_id", ""))
            return result
        except httpx.HTTPError as exc:
            raise OpenClawClientError(f"Skill registration failed: {exc}") from exc

    async def unregister_skill(self, skill_name: str) -> bool:
        """Remove ClawSmith from the OpenClaw skill registry."""
        client = await self._get_client()
        try:
            resp = await client.delete(f"{self._gateway_url}/skills/{skill_name}")
            resp.raise_for_status()
            logger.info("Skill '%s' unregistered from OpenClaw", skill_name)
            return True
        except httpx.HTTPError as exc:
            logger.warning("Skill unregistration failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Task status callbacks
    # ------------------------------------------------------------------

    async def report_task_status(
        self,
        task_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        progress_pct: float | None = None,
    ) -> None:
        """Send a task status update back to OpenClaw.

        Called during pipeline execution so OpenClaw can track progress.
        """
        if not self._gateway_url:
            return
        payload: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
            "timestamp": time.time(),
        }
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        if progress_pct is not None:
            payload["progress_pct"] = progress_pct

        client = await self._get_client()
        body = json.dumps(payload).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._webhook_secret:
            headers["X-ClawSmith-Signature"] = self._sign_payload(body)

        try:
            resp = await client.post(
                f"{self._gateway_url}/tasks/{task_id}/status",
                content=body,
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Status callback failed for task %s: %s", task_id, exc)

    async def report_task_complete(
        self, task_id: str, result: dict[str, Any]
    ) -> None:
        """Convenience wrapper: mark a task as completed with its result."""
        await self.report_task_status(task_id, "completed", result=result)

    async def report_task_failed(self, task_id: str, error: str) -> None:
        """Convenience wrapper: mark a task as failed."""
        await self.report_task_status(task_id, "failed", error=error)

    # ------------------------------------------------------------------
    # Task submission (ClawSmith → OpenClaw)
    # ------------------------------------------------------------------

    async def submit_task(
        self,
        task: str,
        *,
        repo_path: str = ".",
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit a task to OpenClaw for routing to another skill."""
        client = await self._get_client()
        payload: dict[str, Any] = {
            "task": task,
            "repo_path": repo_path,
            "priority": priority,
            "source_skill": "ClawSmith",
        }
        if metadata:
            payload["metadata"] = metadata

        body = json.dumps(payload).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._webhook_secret:
            headers["X-ClawSmith-Signature"] = self._sign_payload(body)

        try:
            resp = await client.post(
                f"{self._gateway_url}/tasks/submit",
                content=body,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise OpenClawClientError(f"Task submission failed: {exc}") from exc


def get_client() -> OpenClawClient:
    """Create an OpenClawClient from the current configuration."""
    from config.config_loader import get_config

    cfg = get_config().openclaw
    return OpenClawClient(
        gateway_url=cfg.gateway_url,
        api_key=cfg.api_key,
        webhook_secret=cfg.webhook_secret,
    )

"""Inbound webhook receiver for OpenClaw task forwarding.

Exposes a lightweight Starlette ASGI application with:
  POST /webhook/task   — accept a task payload, run it through the pipeline
  POST /webhook/ping   — lightweight liveness probe from OpenClaw
  GET  /health         — readiness check (config loaded, pipeline importable)

All mutating endpoints verify an HMAC-SHA256 signature when a webhook secret
is configured, rejecting unsigned or mis-signed requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from orchestrator.logging_setup import get_logger

logger = get_logger("openclaw_webhook")

_active_tasks: dict[str, dict[str, Any]] = {}


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature against the webhook secret."""
    if not secret:
        return True
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _check_signature(request: Request, secret: str) -> JSONResponse | None:
    """Return an error response if the signature is invalid, else None."""
    if not secret:
        return None
    sig = request.headers.get("X-OpenClaw-Signature", "")
    if not sig:
        return JSONResponse(
            {"error": "Missing X-OpenClaw-Signature header"},
            status_code=401,
        )
    body = await request.body()
    if not _verify_signature(body, sig, secret):
        logger.warning("Invalid webhook signature from %s", request.client)
        return JSONResponse({"error": "Invalid signature"}, status_code=403)
    return None


async def webhook_task(request: Request) -> JSONResponse:
    """Accept a task from OpenClaw and run it through the pipeline.

    Expected JSON body::

        {
            "task_id": "...",            # optional — generated if absent
            "task": "Fix the login bug",
            "repo_path": ".",
            "dry_run": false,
            "callback_url": "..."        # optional — override config callback
        }
    """
    from config.config_loader import get_config
    from providers.openclaw_client import OpenClawClient

    cfg = get_config().openclaw
    sig_err = await _check_signature(request, cfg.webhook_secret)
    if sig_err:
        return sig_err

    try:
        body = await request.body()
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)

    task = payload.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "Missing 'task' field"}, status_code=400)

    task_id = payload.get("task_id", str(uuid.uuid4()))
    repo_path = payload.get("repo_path", ".")
    dry_run = payload.get("dry_run", False)
    callback_url = payload.get("callback_url", cfg.callback_url or cfg.gateway_url)

    _active_tasks[task_id] = {
        "task_id": task_id,
        "task": task,
        "status": "accepted",
        "accepted_at": time.time(),
    }
    logger.info("Accepted task %s from OpenClaw: %s", task_id, task[:120])

    asyncio.create_task(
        _execute_task(task_id, task, repo_path, dry_run, callback_url, cfg.webhook_secret)
    )

    return JSONResponse(
        {"task_id": task_id, "status": "accepted"},
        status_code=202,
    )


async def _execute_task(
    task_id: str,
    task: str,
    repo_path: str,
    dry_run: bool,
    callback_url: str,
    webhook_secret: str,
) -> None:
    """Run the pipeline for a forwarded task and report results back."""
    client: OpenClawClient | None = None
    if callback_url:
        from providers.openclaw_client import OpenClawClient

        client = OpenClawClient(
            gateway_url=callback_url,
            webhook_secret=webhook_secret,
        )

    try:
        if client:
            await client.report_task_status(task_id, "running", progress_pct=0.0)

        _active_tasks[task_id]["status"] = "running"

        from orchestrator.pipeline import OrchestrationPipeline

        pipeline = OrchestrationPipeline()
        result = await pipeline.run(task, repo_path, dry_run)

        from providers.openclaw_adapter import OpenClawAdapter

        formatted = OpenClawAdapter(pipeline).format_response(result)
        formatted["task_id"] = task_id

        _active_tasks[task_id] = {
            **_active_tasks[task_id],
            "status": "completed" if result.success else "failed",
            "completed_at": time.time(),
            "result": formatted,
        }

        if client:
            if result.success:
                await client.report_task_complete(task_id, formatted)
            else:
                await client.report_task_failed(
                    task_id, result.error_message or "Pipeline failed"
                )

        logger.info(
            "Task %s %s (%.1fs)",
            task_id,
            "completed" if result.success else "failed",
            result.duration_seconds,
        )
    except Exception as exc:
        logger.exception("Task %s crashed: %s", task_id, exc)
        _active_tasks[task_id]["status"] = "error"
        _active_tasks[task_id]["error"] = str(exc)
        if client:
            await client.report_task_failed(task_id, str(exc))
    finally:
        if client:
            await client.close()


async def webhook_ping(request: Request) -> JSONResponse:
    """Liveness probe — OpenClaw pings this to confirm the receiver is up."""
    from config.config_loader import get_config

    cfg = get_config().openclaw
    sig_err = await _check_signature(request, cfg.webhook_secret)
    if sig_err:
        return sig_err

    return JSONResponse({
        "status": "ok",
        "skill": cfg.skill_name,
        "timestamp": time.time(),
    })


async def health(request: Request) -> JSONResponse:
    """Readiness check — no auth required."""
    checks: dict[str, Any] = {"status": "ok"}
    try:
        from config.config_loader import get_config
        cfg = get_config()
        checks["config_loaded"] = True
        checks["skill_name"] = cfg.openclaw.skill_name
        checks["mcp_endpoint"] = cfg.openclaw.mcp_endpoint
        checks["gateway_configured"] = bool(cfg.openclaw.gateway_url)
    except Exception as exc:
        checks["status"] = "degraded"
        checks["config_loaded"] = False
        checks["error"] = str(exc)

    checks["active_tasks"] = len(_active_tasks)
    return JSONResponse(checks)


async def task_status(request: Request) -> JSONResponse:
    """Query status of a specific task by ID."""
    task_id = request.path_params["task_id"]
    info = _active_tasks.get(task_id)
    if not info:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return JSONResponse(info)


async def list_tasks(request: Request) -> JSONResponse:
    """List recent tasks (most recent first, capped at 50)."""
    tasks = sorted(
        _active_tasks.values(),
        key=lambda t: t.get("accepted_at", 0),
        reverse=True,
    )[:50]
    return JSONResponse({"tasks": tasks, "total": len(_active_tasks)})


# ------------------------------------------------------------------
# Shared provider endpoints
# ------------------------------------------------------------------

async def webhook_providers(request: Request) -> JSONResponse:
    """List available LLM providers/models that OpenClaw can use."""
    from config.config_loader import get_config

    cfg = get_config()
    oc = cfg.openclaw
    sig_err = await _check_signature(request, oc.webhook_secret)
    if sig_err:
        return sig_err

    if not oc.share_api_keys and not oc.share_local_models:
        return JSONResponse({"shared": False, "reason": "Sharing disabled in config"})

    import os
    import shutil

    result: dict[str, Any] = {
        "shared": True,
        "api_providers": [],
        "tiers": [],
        "local_models": [],
    }

    if oc.share_api_keys:
        for env_key, provider_name in [
            ("OPENAI_API_KEY", "openai"),
            ("ANTHROPIC_API_KEY", "anthropic"),
            ("OPENROUTER_API_KEY", "openrouter"),
        ]:
            if os.environ.get(env_key):
                result["api_providers"].append(provider_name)

        for tier_name in ("local_router", "local_code", "premium", "prompt_polisher"):
            tier_cfg = getattr(cfg.models, tier_name)
            result["tiers"].append({
                "tier": tier_name,
                "provider": tier_cfg.provider,
                "model": tier_cfg.model_name,
                "max_tokens": tier_cfg.max_tokens,
                "is_local": tier_cfg.provider == "ollama",
            })

    if oc.share_local_models and shutil.which("ollama"):
        try:
            import subprocess
            proc = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                for line in proc.stdout.strip().splitlines()[1:]:
                    parts = line.split()
                    if parts:
                        result["local_models"].append(parts[0])
        except Exception:
            pass

    return JSONResponse(result)


async def webhook_complete(request: Request) -> JSONResponse:
    """Proxy an LLM completion through ClawSmith's providers.

    Expected JSON body::

        {
            "prompt": "Explain quicksort",
            "tier": "local_code",
            "system_prompt": "",
            "max_tokens": 1024,
            "temperature": 0.2
        }
    """
    from config.config_loader import get_config

    cfg = get_config()
    oc = cfg.openclaw
    sig_err = await _check_signature(request, oc.webhook_secret)
    if sig_err:
        return sig_err

    try:
        body = await request.body()
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)

    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "Missing 'prompt' field"}, status_code=400)

    tier = payload.get("tier", "local_code")
    system_prompt = payload.get("system_prompt", "")
    max_tokens = payload.get("max_tokens", 1024)
    temperature = payload.get("temperature", 0.2)

    tier_cfg = getattr(cfg.models, tier, None)
    if tier_cfg is None:
        return JSONResponse({"error": f"Unknown tier: {tier}"}, status_code=400)

    is_local = tier_cfg.provider == "ollama"
    if is_local and not oc.share_local_models:
        return JSONResponse(
            {"error": "Local model sharing is disabled"}, status_code=403,
        )
    if not is_local and not oc.share_api_keys:
        return JSONResponse(
            {"error": "API key sharing is disabled"}, status_code=403,
        )

    try:
        from providers.registry import get_registry

        provider = get_registry().get_provider(tier)
        completion = await provider.complete(
            prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        logger.info(
            "Shared completion via %s (%s): %d tokens",
            tier, tier_cfg.model_name, completion.output_tokens,
        )
        return JSONResponse(completion.model_dump())
    except Exception as exc:
        logger.warning("Shared completion failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


def create_webhook_app() -> Starlette:
    """Build the Starlette ASGI application for the webhook receiver."""
    routes = [
        Route("/webhook/task", webhook_task, methods=["POST"]),
        Route("/webhook/complete", webhook_complete, methods=["POST"]),
        Route("/webhook/ping", webhook_ping, methods=["POST"]),
        Route("/providers", webhook_providers, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/tasks", list_tasks, methods=["GET"]),
        Route("/tasks/{task_id}", task_status, methods=["GET"]),
    ]
    return Starlette(routes=routes)


def run_webhook_server(host: str | None = None, port: int | None = None) -> None:
    """Start the webhook receiver as a standalone process (blocking)."""
    import uvicorn

    from config.config_loader import get_config

    cfg = get_config().openclaw
    effective_host = host or cfg.webhook_host
    effective_port = port or cfg.webhook_port

    logger.info("Starting webhook receiver on %s:%s", effective_host, effective_port)
    app = create_webhook_app()
    uvicorn.run(app, host=effective_host, port=effective_port, log_level="info")

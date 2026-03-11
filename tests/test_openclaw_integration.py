"""Tests for OpenClaw integration: webhook, client, adapter, and config."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from config.config_loader import OpenClawConfig
from providers.openclaw_adapter import OPENCLAW_TOOL_DEFINITIONS, OpenClawAdapter
from providers.openclaw_client import OpenClawClient, OpenClawClientError
from providers.openclaw_webhook import (
    _verify_signature,
    create_webhook_app,
)


# ---------------------------------------------------------------------------
# HMAC signature verification
# ---------------------------------------------------------------------------

class TestHMACVerification:
    def test_valid_signature(self):
        secret = "test-secret-123"
        body = b'{"task": "hello"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_signature(body, sig, secret) is True

    def test_invalid_signature(self):
        secret = "test-secret-123"
        body = b'{"task": "hello"}'
        assert _verify_signature(body, "bad-signature", secret) is False

    def test_empty_secret_always_passes(self):
        body = b'{"task": "hello"}'
        assert _verify_signature(body, "", "") is True
        assert _verify_signature(body, "anything", "") is True

    def test_tampered_body_fails(self):
        secret = "test-secret-123"
        original = b'{"task": "hello"}'
        sig = hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()
        tampered = b'{"task": "evil"}'
        assert _verify_signature(tampered, sig, secret) is False


# ---------------------------------------------------------------------------
# Webhook receiver (via Starlette TestClient)
# ---------------------------------------------------------------------------

class TestWebhookReceiver:
    @pytest.fixture()
    def app(self):
        return create_webhook_app()

    @pytest.fixture()
    def client(self, app):
        return TestClient(app)

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")

    def test_webhook_ping(self, client):
        resp = client.post("/webhook/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_webhook_task_missing_body(self, client):
        resp = client.post("/webhook/task", content=b"not json")
        assert resp.status_code == 400

    def test_webhook_task_missing_task_field(self, client):
        resp = client.post(
            "/webhook/task",
            json={"repo_path": "."},
        )
        assert resp.status_code == 400
        assert "Missing 'task'" in resp.json()["error"]

    @patch("providers.openclaw_webhook.asyncio.create_task")
    def test_webhook_task_accepted(self, mock_create_task, client):
        resp = client.post(
            "/webhook/task",
            json={"task": "Fix the login bug", "repo_path": "."},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "task_id" in data
        mock_create_task.assert_called_once()

    @patch("providers.openclaw_webhook.asyncio.create_task")
    def test_webhook_task_with_custom_id(self, mock_create_task, client):
        resp = client.post(
            "/webhook/task",
            json={
                "task": "Refactor auth",
                "task_id": "custom-123",
                "repo_path": ".",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["task_id"] == "custom-123"

    def test_webhook_rejects_bad_signature(self, client):
        with patch("config.config_loader.get_config") as mock_cfg:
            oc = OpenClawConfig(webhook_secret="my-secret")
            mock_cfg.return_value = MagicMock(openclaw=oc)

            resp = client.post(
                "/webhook/ping",
                headers={"X-OpenClaw-Signature": "wrong"},
                content=b"{}",
            )
            assert resp.status_code == 403

    def test_webhook_requires_signature_header_when_secret_set(self, client):
        with patch("config.config_loader.get_config") as mock_cfg:
            oc = OpenClawConfig(webhook_secret="my-secret")
            mock_cfg.return_value = MagicMock(openclaw=oc)

            resp = client.post("/webhook/ping", content=b"{}")
            assert resp.status_code == 401

    def test_list_tasks_initially_empty(self, client):
        from providers import openclaw_webhook
        openclaw_webhook._active_tasks.clear()

        resp = client.get("/tasks")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []

    def test_task_status_not_found(self, client):
        resp = client.get("/tasks/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# OpenClaw client
# ---------------------------------------------------------------------------

class TestOpenClawClient:
    def test_sign_payload(self):
        client = OpenClawClient(
            gateway_url="http://localhost:9000",
            webhook_secret="secret",
        )
        body = b"test-body"
        expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert client._sign_payload(body) == expected

    @pytest.mark.asyncio
    async def test_ping_no_gateway(self):
        client = OpenClawClient(gateway_url="")
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_ping_unreachable(self):
        client = OpenClawClient(gateway_url="http://localhost:1", timeout=0.5)
        assert await client.ping() is False
        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_creates_httpx_client(self):
        client = OpenClawClient(
            gateway_url="http://example.com",
            api_key="test-key",
        )
        http_client = await client._get_client()
        assert http_client is not None
        assert "Bearer test-key" in http_client.headers.get("authorization", "")
        await client.close()

    @pytest.mark.asyncio
    async def test_report_task_status_noop_without_gateway(self):
        client = OpenClawClient(gateway_url="")
        await client.report_task_status("task-1", "running")

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        client = OpenClawClient(gateway_url="http://example.com")
        await client.close()
        await client.close()


# ---------------------------------------------------------------------------
# OpenClaw adapter
# ---------------------------------------------------------------------------

class TestOpenClawAdapter:
    def test_build_skill_manifest_structure(self):
        adapter = OpenClawAdapter()
        manifest = adapter.build_skill_manifest()

        assert manifest["name"] == "ClawSmith"
        assert "mcp_endpoint" in manifest
        assert "webhook_endpoint" in manifest
        assert "tools" in manifest
        assert len(manifest["tools"]) == len(OPENCLAW_TOOL_DEFINITIONS)
        assert "capabilities" in manifest
        assert "task_forwarding" in manifest["capabilities"]

    def test_tool_definitions_have_required_fields(self):
        for tool in OPENCLAW_TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input" in tool
            assert tool["name"].strip()

    def test_no_stale_cursor_tool_names(self):
        names = [t["name"] for t in OPENCLAW_TOOL_DEFINITIONS]
        assert "cursor_run_job" not in names
        assert "cursor_run_bat" not in names
        assert "agent_run_job" in names
        assert "agent_run_bat" in names

    def test_format_response_success(self):
        from orchestrator.schemas import ModelTier, PipelineResult, RoutingDecision

        rd = RoutingDecision(
            selected_tier=ModelTier.local_code,
            model_name="ollama/codellama",
            provider="ollama",
            reasoning="Local model for simple task",
            confidence_score=0.9,
            estimated_tokens=500,
            estimated_cost_usd=0.0,
        )
        result = PipelineResult(
            task_description="test",
            repo_path=".",
            dry_run=False,
            success=True,
            routing_decision=rd,
            generated_prompt="do stuff",
            duration_seconds=2.5,
        )
        formatted = OpenClawAdapter.format_response(result)

        assert formatted["success"] is True
        assert formatted["model_used"] == "ollama/codellama"
        assert formatted["tier"] == "local_code"
        assert formatted["duration_seconds"] == 2.5
        assert formatted["error"] is None

    def test_format_response_failure(self):
        from orchestrator.schemas import PipelineResult

        result = PipelineResult(
            task_description="test",
            repo_path=".",
            dry_run=False,
            success=False,
            error_message="Provider timeout",
            duration_seconds=30.0,
        )
        formatted = OpenClawAdapter.format_response(result)

        assert formatted["success"] is False
        assert formatted["error"] == "Provider timeout"

    def test_register_as_skill_writes_file(self, tmp_path):
        output = tmp_path / "SKILL.md"
        adapter = OpenClawAdapter()
        result = adapter.register_as_skill(output)

        assert result == output
        assert output.exists()
        content = output.read_text()
        assert "ClawSmith" in content
        assert "## Webhook Endpoint" in content
        assert "HMAC-SHA256" in content
        assert "openclaw_forward_task" in content

    def test_manifest_includes_new_tools(self):
        adapter = OpenClawAdapter()
        manifest = adapter.build_skill_manifest()
        tool_names = [t["name"] for t in manifest["tools"]]
        assert "openclaw_forward_task" in tool_names
        assert "machine_profile" in tool_names
        assert "workspace_graph" in tool_names
        assert "mutation_propose" in tool_names
        assert "shared_providers" in tool_names
        assert "shared_complete" in tool_names


# ---------------------------------------------------------------------------
# Shared providers
# ---------------------------------------------------------------------------

class TestSharedProviders:
    def test_manifest_includes_shared_providers_by_default(self):
        adapter = OpenClawAdapter()
        manifest = adapter.build_skill_manifest()
        assert "shared_providers" in manifest
        sp = manifest["shared_providers"]
        assert sp["api_keys_shared"] is True
        assert sp["local_models_shared"] is True
        assert sp["complete_endpoint"] is not None
        assert "/webhook/complete" in sp["complete_endpoint"]

    def test_manifest_includes_tier_info(self):
        adapter = OpenClawAdapter()
        manifest = adapter.build_skill_manifest()
        tiers = manifest["shared_providers"].get("tiers", [])
        assert len(tiers) == 4
        tier_names = [t["tier"] for t in tiers]
        assert "local_router" in tier_names
        assert "premium" in tier_names

    def test_manifest_shared_llm_capability(self):
        adapter = OpenClawAdapter()
        manifest = adapter.build_skill_manifest()
        assert "shared_llm_provider" in manifest["capabilities"]

    def test_skill_md_includes_shared_section(self, tmp_path):
        output = tmp_path / "SKILL.md"
        adapter = OpenClawAdapter()
        adapter.register_as_skill(output)
        content = output.read_text()
        assert "## Shared LLM Providers" in content
        assert "shared_complete" in content
        assert "shared_providers" in content

    def test_api_providers_detected(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            adapter = OpenClawAdapter()
            manifest = adapter.build_skill_manifest()
            providers = manifest["shared_providers"].get("api_providers", [])
            assert "openai" in providers

    def test_webhook_complete_missing_prompt(self):
        app = create_webhook_app()
        client = TestClient(app)
        resp = client.post("/webhook/complete", json={"tier": "local_code"})
        assert resp.status_code == 400
        assert "prompt" in resp.json()["error"].lower()

    def test_webhook_complete_unknown_tier(self):
        app = create_webhook_app()
        client = TestClient(app)
        resp = client.post(
            "/webhook/complete",
            json={"prompt": "Hello", "tier": "nonexistent"},
        )
        assert resp.status_code == 400
        assert "Unknown tier" in resp.json()["error"]

    def test_webhook_complete_blocked_when_sharing_disabled(self):
        app = create_webhook_app()
        client = TestClient(app)
        with patch("config.config_loader.get_config") as mock_cfg:
            oc = OpenClawConfig(share_api_keys=False, share_local_models=False)
            mock_cfg.return_value = MagicMock(
                openclaw=oc,
                models=MagicMock(
                    premium=MagicMock(provider="openai"),
                ),
            )
            resp = client.post(
                "/webhook/complete",
                json={"prompt": "Hello", "tier": "premium"},
            )
            assert resp.status_code == 403

    def test_webhook_providers_when_sharing_disabled(self):
        app = create_webhook_app()
        client = TestClient(app)
        with patch("config.config_loader.get_config") as mock_cfg:
            oc = OpenClawConfig(share_api_keys=False, share_local_models=False)
            mock_cfg.return_value = MagicMock(openclaw=oc)
            resp = client.get("/providers")
            assert resp.status_code == 200
            assert resp.json()["shared"] is False

    def test_webhook_providers_endpoint_returns_data(self):
        app = create_webhook_app()
        client = TestClient(app)
        resp = client.get("/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["shared"] is True
        assert "tiers" in data
        assert "api_providers" in data


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

class TestOpenClawConfig:
    def test_defaults(self):
        cfg = OpenClawConfig()
        assert cfg.skill_name == "ClawSmith"
        assert cfg.webhook_port == 8766
        assert cfg.webhook_host == "127.0.0.1"
        assert cfg.gateway_url == ""
        assert cfg.auto_register is False
        assert cfg.task_timeout == 600
        assert cfg.share_api_keys is True
        assert cfg.share_local_models is True

    def test_custom_values(self):
        cfg = OpenClawConfig(
            gateway_url="https://openclaw.example.com",
            api_key="sk-123",
            webhook_secret="secret",
            webhook_port=9999,
            auto_register=True,
        )
        assert cfg.gateway_url == "https://openclaw.example.com"
        assert cfg.api_key == "sk-123"
        assert cfg.webhook_port == 9999
        assert cfg.auto_register is True

    def test_sharing_can_be_disabled(self):
        cfg = OpenClawConfig(share_api_keys=False, share_local_models=False)
        assert cfg.share_api_keys is False
        assert cfg.share_local_models is False

    def test_config_loads_from_yaml(self, sample_config_yaml):
        from config.config_loader import load_config

        cfg = load_config(sample_config_yaml)
        assert cfg.openclaw.skill_name == "ClawSmith"
        assert cfg.openclaw.webhook_port == 8766
        assert cfg.openclaw.gateway_url == ""

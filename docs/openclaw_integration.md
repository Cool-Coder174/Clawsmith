# OpenClaw Integration Guide

## What is OpenClaw?

OpenClaw is an outer gateway / orchestration surface that routes tasks from various
channels (chat, webhooks, scheduled jobs) to specialised AI skills. Each skill
exposes a set of tools via the Model Context Protocol (MCP) so that OpenClaw can
discover and invoke them on behalf of users.

## Integration Model

ClawSmith integrates with OpenClaw through three channels:

1. **MCP Tools** — OpenClaw discovers ClawSmith's capabilities via `SKILL.md` or
   `config/openclaw_skill.yaml` and calls MCP tools over SSE.
2. **Webhook Receiver** — OpenClaw POSTs task payloads to ClawSmith's webhook
   endpoint for asynchronous execution with status callbacks.
3. **Gateway Client** — ClawSmith registers itself with the OpenClaw gateway,
   sends status updates during task execution, and can submit tasks back to
   OpenClaw for routing to other skills.

```
                  ┌──────────────────────────────────┐
                  │           OpenClaw Gateway        │
                  └───────┬────────────┬─────────────┘
                          │            │
            SSE (MCP)     │            │  POST /webhook/task
                          ▼            ▼
              ┌────────────────┐  ┌─────────────────────┐
              │  MCP Server    │  │  Webhook Receiver    │
              │  :8765/sse     │  │  :8766/webhook/task  │
              └───────┬────────┘  └──────────┬──────────┘
                      │                      │
                      ▼                      ▼
              ┌──────────────────────────────────────┐
              │     OrchestrationPipeline             │
              │  audit → map → classify → route →    │
              │  prompt → dispatch → execute → verify │
              └──────────────────────────────────────┘
                      │
                      ▼  Status callbacks (optional)
              ┌──────────────────────────────────────┐
              │  OpenClawClient → gateway /tasks/status│
              └──────────────────────────────────────┘
```

## Quick Start

### 1. Configure OpenClaw Settings

In `config/settings.yaml`:

```yaml
openclaw:
  skill_name: ClawSmith
  mcp_endpoint: "http://127.0.0.1:8765/sse"
  webhook_secret: "your-shared-secret"
  gateway_url: "https://openclaw.example.com"
  api_key: "your-openclaw-api-key"
  webhook_port: 8766
  webhook_host: "127.0.0.1"
  auto_register: false
  task_timeout: 600
```

Or set via environment variables in `.env`:

```bash
OPENCLAW_WEBHOOK_SECRET=your-shared-secret
OPENCLAW_API_KEY=your-openclaw-api-key
OPENCLAW_GATEWAY_URL=https://openclaw.example.com
```

### 2. Start ClawSmith with Webhook

```bash
# Start both MCP server and webhook receiver
clawsmith start --webhook

# Or start them separately
clawsmith start-server       # MCP server on :8765
clawsmith openclaw webhook   # Webhook receiver on :8766
```

### 3. Register the Skill

```bash
# Generate SKILL.md locally
clawsmith openclaw register --output SKILL.md

# Generate SKILL.md and push to OpenClaw gateway
clawsmith openclaw register --remote
```

### 4. Verify Integration

```bash
# Check OpenClaw settings and gateway connectivity
clawsmith openclaw status

# Ping the OpenClaw gateway
clawsmith openclaw ping

# View the skill manifest JSON
clawsmith openclaw manifest

# Full doctor check (includes OpenClaw checks)
clawsmith doctor
```

## Webhook API

### `POST /webhook/task`

Accept a task from OpenClaw and run it through the pipeline asynchronously.

**Request:**
```json
{
    "task_id": "abc-123",
    "task": "Fix the login bug in auth.py",
    "repo_path": ".",
    "dry_run": false,
    "callback_url": "https://openclaw.example.com"
}
```

**Response (202 Accepted):**
```json
{
    "task_id": "abc-123",
    "status": "accepted"
}
```

The task runs asynchronously. Status updates are sent back to the `callback_url`
(or the configured `gateway_url`) via the OpenClaw client.

### `POST /webhook/ping`

Liveness probe. OpenClaw pings this to confirm the receiver is alive.

### `GET /health`

Readiness check (no auth required). Returns config status and active task count.

### `GET /tasks`

List recent tasks (most recent first, capped at 50).

### `GET /tasks/{task_id}`

Query the status of a specific task.

### `GET /providers`

List available LLM providers and models that OpenClaw can use via ClawSmith.
Returns which API key providers are configured (without exposing keys), model
tiers, and locally installed models.

### `POST /webhook/complete`

Proxy an LLM completion through ClawSmith's providers. OpenClaw can use this
to access API keys and local models without needing its own credentials.

**Request:**
```json
{
    "prompt": "Explain quicksort in Python",
    "tier": "local_code",
    "system_prompt": "You are a helpful assistant.",
    "max_tokens": 1024,
    "temperature": 0.2
}
```

**Response (200 OK):**
```json
{
    "text": "Quicksort is a divide-and-conquer algorithm...",
    "input_tokens": 12,
    "output_tokens": 150,
    "model": "ollama/codellama",
    "cost_estimate": 0.0,
    "latency_ms": 1234.5
}
```

Available tiers: `local_router`, `local_code`, `premium`, `prompt_polisher`.
Returns 403 if sharing is disabled for the requested tier type.

## Shared LLM Providers

ClawSmith shares its API keys and local models with OpenClaw **by default**.
When OpenClaw needs to run an LLM completion, it can route the request through
ClawSmith instead of managing its own provider credentials.

### How it works

1. **Discovery**: OpenClaw calls `GET /providers` or the `shared_providers` MCP
   tool to see what's available — API providers (openai, anthropic, openrouter),
   model tiers, and locally installed Ollama models.

2. **Completion**: OpenClaw sends a completion request via `POST /webhook/complete`
   or the `shared_complete` MCP tool, specifying which tier to use.

3. **Routing**: ClawSmith routes the request through its existing provider
   infrastructure (LiteLLM), using its own API keys for cloud providers or its
   local Ollama instance for local models.

### Configuration

Sharing is controlled by two settings in `config/settings.yaml`:

```yaml
openclaw:
  share_api_keys: true        # let OpenClaw use our cloud API keys
  share_local_models: true    # let OpenClaw use our local Ollama models
```

Set either to `false` to disable that type of sharing. When both are `false`,
the `/webhook/complete` endpoint returns 403 and `shared_providers` reports
`"shared": false`.

## Authentication

All mutating webhook endpoints verify HMAC-SHA256 signatures when
`openclaw.webhook_secret` is configured:

1. OpenClaw computes `HMAC-SHA256(secret, request_body)` and sends it in the
   `X-OpenClaw-Signature` header.
2. ClawSmith verifies the signature before processing the request.
3. Requests with missing or invalid signatures are rejected with 401/403.

When no secret is configured, signature verification is skipped (development mode).

## MCP Tools

The following tools are exposed via MCP and available to OpenClaw:

| Tool | Description |
|------|-------------|
| `openclaw_forward_task` | Forward a task through the full pipeline |
| `openclaw_skill_manifest` | Return the skill manifest for registration |
| `shared_providers` | List available providers/models for shared use |
| `shared_complete` | Run an LLM completion through ClawSmith's providers |
| `repo_audit` | Audit a repository |
| `repo_map` | Generate directory tree map |
| `repo_pack_context` | Pack repository context for a task |
| `route_pick_model` | Classify and route to best model tier |
| `cost_estimate` | Estimate cost across all tiers |
| `agent_run_job` | Execute a job via agent CLI |
| `detect_agent_clis` | Detect installed agent CLIs |
| `build_run` | Run build/install commands |
| `tests_run` | Run test commands |
| `prompts_generate_task_prompt` | Generate structured task prompt |

See `config/openclaw_skill.yaml` for the complete tool list.

## Required Environment Variables

| Variable | Purpose |
|---|---|
| `CLAWSMITH_CONFIG_PATH` | Path to ClawSmith's `settings.yaml` |
| `OPENAI_API_KEY` | API key for OpenAI models (premium / polisher) |
| `ANTHROPIC_API_KEY` | Alternative: Anthropic API key |
| `OPENROUTER_API_KEY` | Alternative: OpenRouter API key |
| `OPENCLAW_WEBHOOK_SECRET` | HMAC secret for webhook authentication |
| `OPENCLAW_API_KEY` | API key for OpenClaw gateway communication |
| `OPENCLAW_GATEWAY_URL` | OpenClaw gateway URL |
| `CLAWSMITH_MCP_HOST` | Override MCP server host (default `127.0.0.1`) |
| `CLAWSMITH_MCP_PORT` | Override MCP server port (default `8765`) |

## Architecture

### Source Files

| File | Role |
|------|------|
| `providers/openclaw_adapter.py` | Gateway adapter: `forward_task`, `format_response`, `register_as_skill`, `build_skill_manifest` |
| `providers/openclaw_client.py` | HTTP client for outbound communication: skill registration, status callbacks, gateway pings |
| `providers/openclaw_webhook.py` | Starlette ASGI webhook receiver with HMAC verification |
| `agents/adapters/openclaw_adapter.py` | Agent CLI adapter for the `openclaw` binary |
| `config/openclaw_skill.yaml` | Static skill metadata for OpenClaw discovery |
| `mcp_server/server.py` | Exposes `openclaw_forward_task` and `openclaw_skill_manifest` MCP tools |

### CLI Commands

| Command | Description |
|---------|-------------|
| `clawsmith start --webhook` | Start MCP server + webhook receiver |
| `clawsmith openclaw webhook` | Start webhook receiver standalone |
| `clawsmith openclaw register [--remote]` | Generate SKILL.md, optionally push to gateway |
| `clawsmith openclaw ping` | Test gateway connectivity |
| `clawsmith openclaw status` | Show integration status |
| `clawsmith openclaw manifest` | Print skill manifest JSON |
| `clawsmith register-skill` | Legacy: generate SKILL.md |

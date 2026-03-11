# OpenClaw Integration Guide

## What is OpenClaw?

OpenClaw is an outer gateway / orchestration surface that routes tasks from various
channels (chat, webhooks, scheduled jobs) to specialised AI skills. Each skill
exposes a set of tools via the Model Context Protocol (MCP) so that OpenClaw can
discover and invoke them on behalf of users.

## Integration Model

ClawSmith registers itself as an **OpenClaw skill**. OpenClaw discovers ClawSmith's
capabilities through a SKILL.md artifact (or the `config/openclaw_skill.yaml`
definition) and routes tasks to ClawSmith's MCP server over SSE.

```
OpenClaw  ──SSE──▶  ClawSmith MCP Server  ──▶  Pipeline  ──▶  Provider / Executor
```

## Step-by-Step Setup

### 1. Start the ClawSmith MCP Server

```bash
scripts\windows\start_mcp_server.bat
```

Or via the CLI:

```bash
clawsmith start-server
```

### 2. Register the Skill

```bash
clawsmith register-skill --output SKILL.md
```

This generates a `SKILL.md` file listing all 12 MCP tools, the server endpoint,
and required environment variables.

### 3. Point OpenClaw to ClawSmith

Provide OpenClaw with either:

- The generated `SKILL.md`, **or**
- The `config/openclaw_skill.yaml` file

OpenClaw will parse the tool definitions and register ClawSmith as an available
skill in its routing table.

### 4. Configure the Webhook (Future)

When OpenClaw supports outbound webhooks, configure it to POST task payloads to
ClawSmith's `forward_task` endpoint. The adapter in
`providers/openclaw_adapter.py` already implements the receiving side.

## Required Environment Variables

| Variable | Purpose |
|---|---|
| `CLAWSMITH_CONFIG_PATH` | Path to ClawSmith's `settings.yaml` |
| `OPENAI_API_KEY` | API key for OpenAI models (premium / polisher) |
| `ANTHROPIC_API_KEY` | Alternative: Anthropic API key |
| `OPENROUTER_API_KEY` | Alternative: OpenRouter API key |
| `OPENCLAW_WEBHOOK_SECRET` | (Future) HMAC secret for webhook auth |
| `CLAWSMITH_MCP_HOST` | Override MCP server host (default `127.0.0.1`) |
| `CLAWSMITH_MCP_PORT` | Override MCP server port (default `8765`) |

## Adapter Seam

The file `providers/openclaw_adapter.py` contains the `OpenClawAdapter` class:

- **`forward_task(task, repo_path, dry_run)`** — the primary integration point.
  Accepts a task string (as would arrive from an OpenClaw webhook/channel), feeds
  it into `OrchestrationPipeline.run()`, and returns a flat response dict.

- **`format_response(result)`** — converts a `PipelineResult` into a dict with
  `success`, `model_used`, `tier`, `cost_usd`, `prompt_preview`,
  `execution_exit_code`, and `error` fields.

- **`register_as_skill(output_path)`** — generates the `SKILL.md` artifact.

A future HTTP handler (e.g. a FastAPI route) can wrap `forward_task` to accept
incoming webhook requests over HTTP.

## Concrete Next Steps

1. **Add a `/webhook` HTTP endpoint** using FastAPI or a lightweight ASGI
   framework. The endpoint would deserialise the incoming JSON payload, call
   `OpenClawAdapter().forward_task(...)`, and return the formatted response.

2. **Register the endpoint URL** in OpenClaw's skill configuration so that
   OpenClaw can POST tasks directly.

3. **Implement HMAC verification** of incoming requests using the
   `OPENCLAW_WEBHOOK_SECRET` environment variable for security.

## Known Limitations

- No live OpenClaw credentials are available in this development environment.
- The adapter is fully implemented and tested locally, but the webhook receiver
  is not yet wired to an HTTP server.
- Authentication between OpenClaw and ClawSmith is stubbed (webhook secret is
  empty by default in config).

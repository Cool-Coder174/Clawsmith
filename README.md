# ClawSmith

**Local-first AI orchestration that routes coding tasks to the cheapest capable model — local Ollama for simple work, cloud APIs only when needed.**

ClawSmith detects your hardware, recommends local LLMs, auto-detects installed agent CLIs (Cursor, Claude Code, Gemini, OpenClaw), and routes every task to the right model tier. Simple bug fixes stay on your machine. Complex refactors escalate to GPT-4o or Claude. Everything is exposed as an MCP server for seamless editor integration.

---

## Install

### Quick install

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Cool-Coder174/ClawSmith/main/install.sh | bash

# Windows PowerShell
irm https://raw.githubusercontent.com/Cool-Coder174/ClawSmith/main/install.ps1 | iex
```

### From source

```bash
git clone https://github.com/Cool-Coder174/ClawSmith.git
cd Clawsmith
pip install -e .
```

### Prerequisites

- **Python 3.11+**
- **git**
- **Ollama** *(optional)* — for local model inference ([ollama.com](https://ollama.com))
- At least one **agent CLI** *(optional)* — Cursor, Claude Code, Gemini CLI, or OpenClaw

---

## First Run

```bash
clawsmith onboard       # guided setup: checks prereqs, creates config, sets up dirs
clawsmith doctor        # verify full environment (HEALTHY / DEGRADED / BLOCKED)
clawsmith smoke-test    # quick integration check across all subsystems
```

---

## Usage

```bash
# Interactive chat session
clawsmith chat

# Run a coding task through the full pipeline
clawsmith run-task --task "Fix the login bug in auth.py" --repo-path .

# Dry-run (no API calls, no execution)
clawsmith run-task --task "Refactor the database layer" --repo-path . --dry-run

# Start the MCP server (for Cursor / editor integration)
clawsmith start

# Detect your hardware and recommend local models
clawsmith detect
clawsmith recommend

# Audit a repository
clawsmith audit --repo-path .

# Detect installed agent CLIs
clawsmith detect-agents
```

---

## Why ClawSmith?

Most AI coding tools send everything to expensive cloud APIs. ClawSmith keeps simple tasks local and only escalates when complexity demands it.

| Feature | What it does |
|---|---|
| **Cost-aware routing** | Simple tasks → local Ollama. Complex tasks → GPT-4o / Claude. |
| **Agent-agnostic** | Works with Cursor, Claude Code, Gemini CLI, or OpenClaw. |
| **Local-first hardware detection** | Scans CPU, GPU, RAM; recommends and provisions models automatically. |
| **MCP integration** | Exposes all tools over SSE for editor integration. |
| **Guarded mutations** | Propose → stage → validate → approve → apply → rollback. |
| **Cross-repo scope** | Manages multi-repository awareness with dependency graphs. |

### Model routing

```
complexity < 0.35  →  local_router  (ollama/mistral — fast, free)
0.35 ≤ complexity < 0.70  →  local_code  (ollama/codellama — code-focused)
complexity ≥ 0.70  →  premium  (openai/gpt-4o — highest capability)
```

Ambiguity bumps the tier up. Critical severity overrides to premium.

---

## Architecture

```
clawsmith run-task --task "..."
    │
    ├── RepoAuditor       → detect languages, frameworks, CI
    ├── ContextPacker      → assemble token-budgeted context
    ├── TaskClassifier     → score complexity, ambiguity, severity
    ├── ModelRouter        → select model tier
    ├── AgentRouter        → select best available agent CLI
    ├── PromptGenerator    → build structured prompt
    ├── Provider           → dispatch to LLM (local or cloud)
    └── JobExecutor        → execute via agent CLI
```

Full docs: [docs/architecture.md](docs/architecture.md)

---

## Configuration

Edit `config/settings.yaml` to change model tiers, routing thresholds, execution settings, and MCP server config.

Environment variables override any config value using `CLAWSMITH_<SECTION>__<KEY>`:

```bash
CLAWSMITH_ROUTING__LOW_COMPLEXITY_THRESHOLD=0.5
CLAWSMITH_MODELS__PREMIUM__MAX_TOKENS=16384
```

API keys go in `.env` (created during onboarding):

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Develop from Source

```bash
git clone https://github.com/Cool-Coder174/ClawSmith.git
cd Clawsmith
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check . --fix
ruff format .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full contributor guidelines.

---

## Troubleshooting

```bash
clawsmith doctor    # diagnoses all issues with PASS / WARN / FAIL
```

The doctor output is designed for copy-paste into GitHub issues. See [docs/troubleshooting.md](docs/troubleshooting.md) for common solutions.

---

## All CLI Commands

| Command | Purpose |
|---|---|
| `clawsmith onboard` | Guided first-run setup |
| `clawsmith doctor` | Preflight environment check |
| `clawsmith smoke-test` | Quick integration verification |
| `clawsmith start` | Start the MCP server |
| `clawsmith chat` | Interactive agentic TUI session |
| `clawsmith run-task` | Run the full orchestration pipeline |
| `clawsmith audit` | Audit a repository |
| `clawsmith detect` | Detect hardware and toolchain |
| `clawsmith recommend` | Recommend local LLMs |
| `clawsmith install-model` | Install a local LLM |
| `clawsmith detect-agents` | Show agent CLI capability matrix |
| `clawsmith register-skill` | Generate OpenClaw SKILL.md |
| `clawsmith memory sync` | Sync persistent memory |
| `clawsmith link-repo` | Add repo to workspace graph |
| `clawsmith scope` | View/create scope contracts |
| `clawsmith mutate propose` | Propose a configuration mutation |
| `clawsmith rollback` | Roll back an applied mutation |

---

## Documentation

- [Installation Guide](INSTALL.md)
- [Contributing](CONTRIBUTING.md)
- [Architecture](docs/architecture.md)
- [Agent Profiles](docs/agent_profiles.md)
- [OpenClaw Integration](docs/openclaw_integration.md)
- [Troubleshooting](docs/troubleshooting.md)

---

## License

See [LICENSE](LICENSE).

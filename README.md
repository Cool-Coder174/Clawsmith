# ClawSmith

> 🧠 Local-first AI orchestration for coding tasks

**Route simple work to local Ollama, save cloud spend for harder problems, and expose everything through MCP for editor and agent integrations.**

ClawSmith detects your hardware, recommends local LLMs, auto-detects installed agent CLIs (`Cursor`, `Claude Code`, `Gemini`, `OpenClaw`), and routes every task to the cheapest capable model tier. Small fixes stay on your machine. Bigger refactors escalate only when they need more capability.

---

## ✨ Why ClawSmith?

Most AI coding tools send everything to expensive cloud APIs. ClawSmith keeps simple tasks local and only escalates when complexity demands it.

- 💸 Keep low-complexity work on local models when possible
- 🧭 Route tasks using complexity, ambiguity, and severity
- 🤖 Work with multiple agent CLIs instead of locking into one
- 🖥️ Detect your machine and recommend realistic local model setups
- 🔌 Expose orchestration tools over MCP for editor integration
- 🛡️ Add guardrails for mutations, scoped repo work, and controlled execution

| Feature | What it does |
|---|---|
| **Cost-aware routing** | Simple tasks -> local Ollama. Complex tasks -> GPT-4o / Claude. |
| **Agent-agnostic** | Works with Cursor, Claude Code, Gemini CLI, or OpenClaw. |
| **Local-first hardware detection** | Scans CPU, GPU, RAM; recommends and provisions models automatically. |
| **MCP integration** | Exposes all tools over SSE for editor integration. |
| **Guarded mutations** | Propose -> stage -> validate -> approve -> apply -> rollback. |
| **Cross-repo scope** | Manages multi-repository awareness with dependency graphs. |
| **OpenClaw integration** | Register as a skill, accept webhook tasks, and share local/cloud providers. |

### 🧮 Model Routing

```text
complexity < 0.35               -> local_router    (ollama/mistral - fast, free)
0.35 <= complexity < 0.70       -> local_code      (ollama/codellama - code-focused)
complexity >= 0.70              -> premium         (openai/gpt-4o - highest capability)
```

Ambiguity bumps the tier up. Critical severity overrides to premium.

---

## 🚀 Install

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
- **Ollama** *(optional)* - for local model inference ([ollama.com](https://ollama.com))
- At least one **agent CLI** *(optional)* - Cursor, Claude Code, Gemini CLI, or OpenClaw

---

## 🏁 First Run

```bash
clawsmith onboard       # guided setup: checks prereqs, creates config, sets up dirs
clawsmith doctor        # verify full environment (HEALTHY / DEGRADED / BLOCKED)
clawsmith smoke-test    # quick integration check across all subsystems
```

---

## ⚡ Quick Usage

```bash
# Interactive chat session
clawsmith chat

# Run a coding task through the full pipeline
clawsmith run-task --task "Fix the login bug in auth.py" --repo-path .

# Dry-run (no API calls, no execution)
clawsmith run-task --task "Refactor the database layer" --repo-path . --dry-run

# Start the MCP server (for Cursor / editor integration)
clawsmith start

# Start MCP + OpenClaw webhook receiver
clawsmith start --webhook

# Detect your hardware and recommend local models
clawsmith detect
clawsmith recommend

# Audit a repository
clawsmith audit --repo-path .

# Detect installed agent CLIs
clawsmith detect-agents
```

---

## 🏗️ Architecture

```text
clawsmith run-task --task "..."
    │
    ├── RepoAuditor        -> detect languages, frameworks, CI
    ├── ContextPacker      -> assemble token-budgeted context
    ├── TaskClassifier     -> score complexity, ambiguity, severity
    ├── ModelRouter        -> select model tier
    ├── AgentRouter        -> select best available agent CLI
    ├── PromptGenerator    -> build structured prompt
    ├── Provider           -> dispatch to LLM (local or cloud)
    └── JobExecutor        -> execute via agent CLI
```

Full docs: [docs/architecture.md](docs/architecture.md)

---

## ⚙️ Configuration

Edit `config/settings.yaml` to change model tiers, routing thresholds, execution settings, MCP server config, and OpenClaw integration.

Environment variables override config values using `CLAWSMITH_<SECTION>__<KEY>`:

```bash
CLAWSMITH_ROUTING__LOW_COMPLEXITY_THRESHOLD=0.5
CLAWSMITH_MODELS__PREMIUM__MAX_TOKENS=16384
CLAWSMITH_OPENCLAW__WEBHOOK_PORT=8766
```

API keys go in `.env` (created during onboarding):

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
OPENCLAW_WEBHOOK_SECRET=...
```

### 🐾 OpenClaw

ClawSmith can act as an OpenClaw skill and shared LLM provider:

```bash
clawsmith openclaw status
clawsmith openclaw register --output SKILL.md
clawsmith openclaw webhook
```

See [OpenClaw Integration](docs/openclaw_integration.md) for webhook setup, shared provider access, and skill registration.

---

## 🛠️ Develop From Source

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor guidelines.

---

## 🩺 Troubleshooting

```bash
clawsmith doctor    # diagnoses all issues with PASS / WARN / FAIL
```

The doctor output is designed for copy-paste into GitHub issues. See [docs/troubleshooting.md](docs/troubleshooting.md) for common solutions.

---

## 📚 CLI Commands

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
| `clawsmith register-skill` | Generate OpenClaw `SKILL.md` |
| `clawsmith openclaw status` | Show OpenClaw integration status |
| `clawsmith openclaw register` | Generate and optionally register OpenClaw skill metadata |
| `clawsmith openclaw webhook` | Start the OpenClaw webhook receiver |
| `clawsmith memory sync` | Sync persistent memory |
| `clawsmith link-repo` | Add repo to workspace graph |
| `clawsmith scope` | View/create scope contracts |
| `clawsmith mutate propose` | Propose a configuration mutation |
| `clawsmith rollback` | Roll back an applied mutation |

---

## 📖 Documentation

- [Installation Guide](INSTALL.md)
- [Contributing](CONTRIBUTING.md)
- [Architecture](docs/architecture.md)
- [Agent Profiles](docs/agent_profiles.md)
- [OpenClaw Integration](docs/openclaw_integration.md)
- [Troubleshooting](docs/troubleshooting.md)

---

## 📄 License

See [LICENSE](LICENSE).

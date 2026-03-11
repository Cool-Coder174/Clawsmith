from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskType(StrEnum):
    audit = "audit"
    bugfix = "bugfix"
    implementation = "implementation"
    refactor = "refactor"
    planning = "planning"
    summarization = "summarization"
    debugging = "debugging"
    testing = "testing"
    prompt_polish = "prompt_polish"


class ModelTier(StrEnum):
    local_router = "local_router"
    local_code = "local_code"
    premium = "premium"
    prompt_polisher = "prompt_polisher"


class JobSpec(BaseModel):
    """Agent-agnostic job specification.

    This schema describes a unit of work that can be dispatched to any
    supported agent CLI (Cursor, Claude Code, Gemini, OpenClaw, etc.).
    """

    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    task_type: TaskType
    objective: str
    working_directory: str
    files_in_scope: list[str] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    prompt: str
    prompt_file: str | None = None
    agent_target: str | None = Field(
        default=None,
        description="Agent CLI to use, e.g. 'cursor', 'claude_code', 'gemini_cli'. "
        "None means auto-select.",
    )
    provider_preference: ModelTier = ModelTier.local_code
    model_preference: str | None = Field(
        default=None,
        description="Explicit model name to pass to the agent CLI, if it supports model switching.",
    )
    invocation_mode: str = Field(
        default="headless",
        description="'headless' for non-interactive, 'interactive' for chat mode.",
    )
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    dry_run: bool = False
    retries: int = Field(default=1, ge=0, le=5)
    output_format: str | None = Field(
        default=None, description="'json', 'text', or None for agent default."
    )
    approval_mode: str | None = Field(
        default=None,
        description="Agent-specific approval/sandbox mode, e.g. 'auto', 'manual', 'sandbox'.",
    )
    environment_overrides: dict[str, str] = Field(default_factory=dict)
    artifact_directory: str | None = None
    log_directory: str | None = None


class ContextPacket(BaseModel):
    model_config = ConfigDict(frozen=False)

    task_summary: str
    relevant_files: dict[str, str] = Field(default_factory=dict)
    architecture_summary: str
    build_test_commands: list[str] = Field(default_factory=list)
    recent_errors: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    recommended_steps: list[str] = Field(default_factory=list)
    token_estimate: int = 0


class TaskClassification(BaseModel):
    model_config = ConfigDict(frozen=False)

    task_type: TaskType
    complexity_score: float
    files_likely_touched: int
    ambiguity_score: float
    architectural_impact: float
    failure_severity: float
    estimated_tokens: int


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=False)

    selected_tier: ModelTier
    model_name: str
    provider: str
    reasoning: str
    confidence_score: float
    estimated_tokens: int
    estimated_cost_usd: float = 0.0
    agent_target: str | None = Field(
        default=None,
        description="Selected agent CLI id, if agent routing was performed.",
    )


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=False)

    job_id: str
    exit_code: int
    stdout: str
    stderr: str
    artifacts: list[str] = Field(default_factory=list)
    duration_seconds: float
    success: bool
    error_message: str | None = None
    agent_used: str | None = Field(
        default=None, description="Agent CLI id that executed this job."
    )


class AgentProfile(BaseModel):
    """Profile-based orchestration configuration for agent workers.

    Profiles are agent-agnostic: the ``agent_target`` field specifies which
    CLI adapter to use.  When set to ``None`` or ``"auto"``, the system
    auto-selects the best available agent.
    """

    model_config = ConfigDict(frozen=False)

    name: str = "default"
    description: str = ""
    task_type: TaskType = TaskType.implementation
    working_directory: str = "."
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    prompt_template: str = "agent_task.bat.template"
    variables: dict[str, str] = Field(default_factory=dict)
    agent_target: str | None = Field(
        default=None,
        description="Agent CLI id (e.g. 'cursor', 'claude_code', 'gemini_cli'). "
        "None or 'auto' means auto-select.",
    )
    provider_preference: ModelTier = ModelTier.local_code
    model_preference: str | None = None
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    dry_run: bool = False
    retries: int = Field(default=1, ge=0, le=5)
    allowed_tiers: list[ModelTier] = Field(default_factory=lambda: list(ModelTier))
    system_prompt_override: str | None = None
    output_format: str | None = None
    approval_mode: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class PipelineResult(BaseModel):
    model_config = ConfigDict(frozen=False)

    task_description: str
    repo_path: str
    audit_report: dict = Field(default_factory=dict)
    repo_map: dict = Field(default_factory=dict)
    context_packet: ContextPacket | None = None
    classification: TaskClassification | None = None
    routing_decision: RoutingDecision | None = None
    generated_prompt: str = ""
    completion: dict | None = None
    execution_result: ExecutionResult | None = None
    dry_run: bool = False
    success: bool = True
    error_message: str | None = None
    duration_seconds: float = 0.0
    agent_status: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot from StatusTracker.summary() — phase, verify_stage, elapsed, etc.",
    )


# ---------------------------------------------------------------------------
# YOLO mode schemas
# ---------------------------------------------------------------------------


class YoloPhaseStatus(StrEnum):
    pending = "pending"
    planning = "planning"
    running = "running"
    verifying = "verifying"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"
    paused = "paused"
    skipped = "skipped"


class YoloPhase(BaseModel):
    """A single decomposed phase in a YOLO execution plan."""

    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    index: int = Field(description="0-based position in the execution queue.")
    title: str
    objective: str
    task_type: TaskType = TaskType.implementation
    files_in_scope: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    estimated_complexity: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Fraction 0.0–1.0 reflecting the phase's relative difficulty.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Phase IDs that must complete before this one runs.",
    )
    status: YoloPhaseStatus = YoloPhaseStatus.pending


class ComplexityBucket(StrEnum):
    trivial = "trivial"
    low = "low"
    medium = "medium"
    high = "high"
    epic = "epic"


class ComplexityAnalysis(BaseModel):
    """Result of analysing a goal's complexity to decide decomposition."""

    model_config = ConfigDict(frozen=False)

    bucket: ComplexityBucket
    raw_score: float = Field(ge=0.0, le=1.0)
    recommended_phases: int = Field(ge=1, le=10)
    reasoning: str = ""
    architectural_impact: float = 0.0
    files_likely_touched: int = 0


class YoloPlan(BaseModel):
    """The full decomposed plan for a YOLO run."""

    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    goal: str
    repo_path: str
    complexity: ComplexityAnalysis
    phases: list[YoloPhase] = Field(default_factory=list)
    skip_planning: bool = False
    created_at: float = Field(default_factory=lambda: __import__("time").time())


class YoloConfig(BaseModel):
    """User-facing configuration for a YOLO run."""

    model_config = ConfigDict(frozen=False)

    skip_planning: bool = Field(
        default=False,
        description="Skip the planning phase — go straight to execution.",
    )
    max_retries: int = Field(
        default=2, ge=0, le=5,
        description="Max retry attempts per phase on verification failure.",
    )
    dry_run: bool = False
    agent_target: str | None = None
    timeout_per_phase: int = Field(
        default=600, ge=30, le=7200,
        description="Seconds before a single phase times out.",
    )
    pause_on_failure: bool = Field(
        default=True,
        description="Pause the queue instead of aborting when a phase fails all retries.",
    )


class YoloPhaseResult(BaseModel):
    """Outcome of a single phase in the YOLO queue."""

    model_config = ConfigDict(frozen=False)

    phase_id: str
    phase_index: int
    title: str
    status: YoloPhaseStatus
    attempts: int = 0
    pipeline_result: PipelineResult | None = None
    error_history: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class YoloResult(BaseModel):
    """Aggregate result of a full YOLO execution run."""

    model_config = ConfigDict(frozen=False)

    plan_id: str
    goal: str
    repo_path: str
    phase_results: list[YoloPhaseResult] = Field(default_factory=list)
    total_phases: int = 0
    completed_phases: int = 0
    failed_phases: int = 0
    skipped_phases: int = 0
    success: bool = False
    error_message: str | None = None
    duration_seconds: float = 0.0
    agent_status: dict[str, Any] = Field(default_factory=dict)

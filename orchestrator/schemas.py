from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskType(str, Enum):
    audit = "audit"
    bugfix = "bugfix"
    implementation = "implementation"
    refactor = "refactor"
    planning = "planning"
    summarization = "summarization"
    debugging = "debugging"
    testing = "testing"
    prompt_polish = "prompt_polish"


class ModelTier(str, Enum):
    local_router = "local_router"
    local_code = "local_code"
    premium = "premium"
    prompt_polisher = "prompt_polisher"


class JobSpec(BaseModel):
    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    task_type: TaskType
    objective: str
    working_directory: str
    files_in_scope: list[str] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    prompt: str
    provider_preference: ModelTier = ModelTier.local_code
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    dry_run: bool = False
    retries: int = Field(default=1, ge=0, le=5)


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


class AgentProfile(BaseModel):
    """Profile-based orchestration configuration for agent workers."""

    model_config = ConfigDict(frozen=False)

    name: str = "default"
    description: str = ""
    task_type: TaskType = TaskType.implementation
    working_directory: str = "."
    build_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    prompt_template: str = "cursor_task.bat.template"
    variables: dict[str, str] = Field(default_factory=dict)
    provider_preference: ModelTier = ModelTier.local_code
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    dry_run: bool = False
    retries: int = Field(default=1, ge=0, le=5)
    allowed_tiers: list[ModelTier] = Field(default_factory=lambda: list(ModelTier))
    system_prompt_override: str | None = None
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

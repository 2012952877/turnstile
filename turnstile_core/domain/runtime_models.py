from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr, model_validator

from .models import StrictModel

ModelCapability = Literal["chat", "tools", "vision", "reasoning", "streaming", "embeddings"]
FOUNDRY_INFERENCE_RESOURCE = "https://ai.azure.com"
FOUNDRY_INFERENCE_ROLE_ID = "a97b65f3-24c7-4388-baec-2e87135dc908"


def default_capabilities() -> list[ModelCapability]:
    return ["chat"]


def foundry_runtime_name(account: str, project: str) -> str:
    prefix = "Microsoft Foundry "
    suffix = " via APIM"
    identity = f"{account}/{project}"
    maximum_identity_length = 160 - len(prefix) - len(suffix)
    if len(identity) > maximum_identity_length:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
        identity = f"{identity[: maximum_identity_length - len(digest) - 1]}-{digest}"
    return f"{prefix}{identity}{suffix}"


class ProviderKind(StrEnum):
    GITHUB = "github"
    ANTHROPIC = "anthropic"
    MICROSOFT_FOUNDRY = "microsoft_foundry"
    OPENAI_COMPATIBLE = "openai_compatible"


class BrandKey(StrEnum):
    GENERIC = "generic"
    AMAZON_BEDROCK = "amazon_bedrock"
    ANTHROPIC = "anthropic"
    AZURE_DATABRICKS = "azure_databricks"
    GITHUB = "github"
    MICROSOFT = "microsoft"
    MICROSOFT_FOUNDRY = "microsoft_foundry"
    OPENAI = "openai"


class ModelFamilyKey(StrEnum):
    GENERIC = "generic"
    CLAUDE = "claude"
    COPILOT = "copilot"
    OPENAI = "openai"


class GatewayKind(StrEnum):
    APIM = "apim"
    LITELLM = "litellm"
    DIRECT = "direct"


class RuntimeKind(StrEnum):
    COPILOT_CLI = "copilot_cli"
    FOUNDRY = "foundry"
    OPENAI_COMPATIBLE = "openai_compatible"


class ConnectionAuthMode(StrEnum):
    MANAGED_IDENTITY = "managed_identity"
    API_KEY = "api_key"


class AuthType(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    AZURE_AD = "azure_ad"


class HealthStatus(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class GatewayProfileWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    implementation: GatewayKind
    base_url: str | None = Field(default=None, max_length=2000)
    auth_type: AuthType = AuthType.NONE
    credential: SecretStr | None = None
    enabled: bool = True
    is_default: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class GatewayProfile(GatewayProfileWrite):
    id: UUID
    created_at: datetime
    updated_at: datetime
    credential: None = None
    credential_configured: bool
    credential_hint: str | None


class ProviderWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    provider_kind: ProviderKind
    endpoint_url: str | None = Field(default=None, max_length=2000)
    auth_type: AuthType = AuthType.NONE
    credential: SecretStr | None = None
    enabled: bool = True
    brand_key: BrandKey = BrandKey.GENERIC
    config: dict[str, Any] = Field(default_factory=dict)


class Provider(ProviderWrite):
    id: UUID
    created_at: datetime
    updated_at: datetime
    credential: None = None
    credential_configured: bool
    credential_hint: str | None


class RuntimeWrite(StrictModel):
    provider_id: UUID
    gateway_profile_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    runtime_kind: RuntimeKind
    enabled: bool = True
    is_default: bool = False
    brand_key: BrandKey = BrandKey.GENERIC
    config: dict[str, Any] = Field(default_factory=dict)
    allowed_roles: list[str] = Field(default_factory=lambda: ["owner", "admin", "member"])


class ProviderTarget(StrictModel):
    existing_id: UUID | None = None
    template: Literal["amazon_bedrock", "microsoft_foundry"] | None = None

    @model_validator(mode="after")
    def require_existing_or_new_provider(self) -> ProviderTarget:
        if (self.existing_id is None) == (self.template is None):
            raise ValueError("select an existing provider or one provider template")
        return self


class ModelConnectionCreate(StrictModel):
    gateway_profile_id: UUID
    provider: ProviderTarget
    auth_mode: ConnectionAuthMode | None = None
    foundry_project_endpoint: HttpUrl | None = None
    foundry_inference_endpoint: HttpUrl | None = None
    bedrock_runtime_url: HttpUrl | None = None

    @model_validator(mode="after")
    def require_one_connection_shape(self) -> ModelConnectionCreate:
        has_foundry = self.foundry_project_endpoint is not None
        has_bedrock = self.bedrock_runtime_url is not None
        if has_foundry == has_bedrock:
            raise ValueError("select one Foundry or Bedrock connection")
        if has_bedrock:
            if self.auth_mode is not None or self.foundry_inference_endpoint is not None:
                raise ValueError("a Bedrock connection accepts only its Runtime URL")
            return self
        if self.auth_mode is None:
            raise ValueError("a Foundry connection requires an authentication mode")
        if self.auth_mode is ConnectionAuthMode.MANAGED_IDENTITY:
            if self.foundry_inference_endpoint is not None:
                raise ValueError(
                    "a managed-identity connection does not use an inference endpoint"
                )
            return self
        if self.foundry_inference_endpoint is None:
            raise ValueError("an API-key connection requires an inference endpoint")
        return self


class ModelConnectionUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    enabled: bool
    is_default: bool

    @model_validator(mode="after")
    def require_enabled_default(self) -> ModelConnectionUpdate:
        if self.is_default and not self.enabled:
            raise ValueError("a default connection must be enabled")
        return self


class Runtime(RuntimeWrite):
    id: UUID
    provider_name: str
    gateway_name: str | None
    health_status: HealthStatus
    health_message: str | None
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ManagedModelWrite(StrictModel):
    provider_id: UUID
    runtime_id: UUID
    model_key: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    family_key: ModelFamilyKey = ModelFamilyKey.GENERIC
    upstream_model_id: str | None = Field(default=None, min_length=1, max_length=500)
    assignment_required: bool = False
    enabled: bool = True
    is_default: bool = False
    capabilities: list[ModelCapability] = Field(default_factory=default_capabilities)
    context_window: int | None = Field(default=None, ge=1)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)
    # Providers bill cached input well below the base rate. None keeps the conservative fallback
    # of charging cached tokens at input_cost_per_million.
    cached_cost_per_million: float | None = Field(default=None, ge=0)
    # Cache writes are billed above the base input rate (Anthropic 1.25x for a 5-minute TTL,
    # OpenAI 1.25x on GPT-5.6 and later). None falls back to the cached rate.
    cache_write_cost_per_million: float | None = Field(default=None, ge=0)
    allowed_roles: list[str] = Field(default_factory=lambda: ["owner", "admin", "member"])


class ManagedModel(ManagedModelWrite):
    id: UUID
    provider_name: str
    runtime_name: str
    publication_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class RegistryResponse(StrictModel):
    providers: list[Provider]
    gateways: list[GatewayProfile]
    runtimes: list[Runtime]
    models: list[ManagedModel]


class RuntimeHealth(StrictModel):
    runtime_id: UUID
    status: HealthStatus
    message: str
    checked_at: datetime


class InvocationMetadata(StrictModel):
    organization_id: str = Field(min_length=1, max_length=255)
    organization: str = Field(min_length=1, max_length=255)
    department_id: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=255)
    project_id: str = Field(min_length=1, max_length=255)
    project: str = Field(min_length=1, max_length=255)
    agent_id: str = Field(min_length=1, max_length=255)
    agent: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=255)
    workflow: str = Field(min_length=1, max_length=255)
    model_id: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    runtime: str = Field(min_length=1, max_length=255)
    request_source: str = Field(min_length=1, max_length=255)
    usage_domain: Literal["apim", "github_copilot"] = "apim"
    run_id: str = Field(min_length=1, max_length=255)
    turn_index: int = Field(default=1, ge=1)


class ToolFunctionCall(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    arguments: str = Field(default="{}", max_length=100_000)


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=255)
    type: Literal["function"] = "function"
    function: ToolFunctionCall


class ToolFunctionDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4_000)
    parameters: dict[str, Any]


class ToolDefinition(StrictModel):
    type: Literal["function"] = "function"
    function: ToolFunctionDefinition


class ChatMessage(StrictModel):
    """One turn in an OpenAI-shaped conversation.

    `content` is optional because an assistant turn that only calls tools carries no
    text, and a `tool` turn carries the result of exactly one call. The validator below
    is what keeps that from degrading into "any field may be missing".
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=200_000)
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=20)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def check_shape_matches_role(self) -> ChatMessage:
        if self.role == "tool":
            if not self.tool_call_id:
                raise ValueError("a tool message must name the call it answers")
            if not self.content:
                raise ValueError("a tool message must carry the tool result")
            return self
        if self.tool_call_id is not None:
            raise ValueError("tool_call_id belongs only to a tool message")
        if self.role == "assistant":
            if not self.content and not self.tool_calls:
                raise ValueError("an assistant message must carry content or tool calls")
            return self
        if self.tool_calls is not None:
            raise ValueError("only an assistant message may carry tool calls")
        if not self.content:
            raise ValueError(f"a {self.role} message must carry content")
        return self


class ModelInvocationRequest(StrictModel):
    metadata: InvocationMetadata
    runtime_id: UUID | None = None
    model_id: UUID | None = None
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    tools: list[ToolDefinition] | None = Field(default=None, max_length=32)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    response_format: Literal["json_object"] | None = None
    stream: bool = False


class InvocationUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    estimated: bool

    @model_validator(mode="after")
    def cache_write_is_a_subset(self) -> InvocationUsage:
        if self.cache_write_tokens > self.cached_tokens:
            raise ValueError("cache_write_tokens cannot exceed cached_tokens")
        return self


class ModelInvocationResponse(StrictModel):
    request_id: str
    correlation_id: str
    content: str
    tool_calls: list[ToolCall] | None = None
    provider: str
    runtime: str
    model: str
    gateway: str
    latency_ms: int = Field(ge=0)
    usage: InvocationUsage | None
    estimated_cost: float | None = Field(default=None, ge=0)


class TrafficGenerationRequest(StrictModel):
    model_ids: list[UUID] = Field(default_factory=list, max_length=20)
    requests_per_model: int = Field(default=4, ge=1, le=100)
    max_output_tokens: int = Field(default=48, ge=1, le=512)
    budget_usd: float = Field(default=5.0, gt=0, le=20)
    dry_run: bool = True


class TrafficPlanItem(StrictModel):
    sequence: int = Field(ge=1)
    model_id: UUID
    model_name: str
    organization_id: str
    organization_name: str
    department_id: str
    department_name: str
    project_id: str
    project_name: str
    agent_id: str
    agent_name: str
    user_id: str
    user_name: str
    workflow: str
    prompt: str
    max_output_tokens: int = Field(ge=1)
    conservative_cost_ceiling: float = Field(ge=0)


class TrafficGenerationPlan(StrictModel):
    dry_run: bool
    request_count: int = Field(ge=0)
    budget_usd: float = Field(gt=0)
    conservative_cost_ceiling: float = Field(ge=0)
    items: list[TrafficPlanItem]


class TrafficExecutionItem(StrictModel):
    sequence: int = Field(ge=1)
    model_id: UUID
    request_id: str | None = None
    correlation_id: str | None = None
    status_code: int = Field(ge=100, le=599)
    estimated_cost: float = Field(ge=0)
    error_message: str | None = None


class TrafficGenerationResult(StrictModel):
    planned_requests: int = Field(ge=0)
    completed_requests: int = Field(ge=0)
    successful_requests: int = Field(ge=0)
    failed_requests: int = Field(ge=0)
    total_estimated_cost: float = Field(ge=0)
    stopped_reason: str | None = None
    items: list[TrafficExecutionItem]

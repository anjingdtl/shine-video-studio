"""The copilot's declarative contract layer (V2.1).

Business modules declare what the product can do; the copilot derives its
knowledge index and capability registry from those declarations at build time.
Nothing here executes anything: this package holds *data*, and one canonical
digest function so a build, a CI check and the runtime all agree on the same
contract identity.

Three rules keep it from becoming a second business system:

* declarations reference the real route, the real Pydantic request model and
  the real executor; they never re-implement validation;
* execution still goes through ``support.actions`` and the original executor;
* a declaration alone never grants execution: ``availability`` must be
  ``AVAILABLE`` and the action must be bound to a reviewed dispatcher.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

APP_VERSION = "2.2.0"
CONTRACT_VERSION = "shine-copilot-contracts-2026.11.17-v22"

PERMISSION_TIERS = {
    "READ": "只读，无需确认",
    "PREVIEW": "先预览差异，再由用户明确确认",
    "EXECUTE_GUARDED": "真实资源消耗，显示影响后由用户明确确认",
    "HUMAN_REQUIRED": "必须由用户在原页面亲自完成",
    "FORBIDDEN": "运行时管家永久不具备",
}

AVAILABILITY = {
    "AVAILABLE": "已审核，可由受控链路委托",
    "REVIEW_REQUIRED": "已发现并收录说明，但尚未通过可执行合同审核",
    "HUMAN_REQUIRED": "只能由用户在原页面完成",
    "NOT_SUPPORTED": "当前版本不代理",
    "FORBIDDEN": "永久禁止",
    "DISCOVERED": "构建期发现的未声明路由，仅作说明",
}

AI_STATUSES = {
    "COVERED": "可由自然语言委托",
    "READ_ONLY": "可查询，不可修改",
    "GUARDED": "需预览并明确授权后委托",
    "HUMAN_REQUIRED": "必须人工确认（AI 只解释）",
    "NOT_SUPPORTED": "当前未接入，附工程原因",
    "FORBIDDEN": "永久禁止",
    "DISCOVERED": "已发现，未纳入受控执行",
    "REVIEW_REQUIRED": "已收录，等待合同审核",
}

IMPLEMENTATION_STATUSES = {
    "IMPLEMENTED": "已实现",
    "PARTIAL": "部分实现",
    "PLANNED": "仅有方案",
    "BLOCKED": "受阻",
    "NOT_SUPPORTED": "当前版本不支持",
}

VERIFICATION_STATUSES = {
    "AUTOMATED": "有自动化测试",
    "HARDWARE_REQUIRED": "需要真机验证",
    "NOT_RUN": "未执行",
    "BLOCKED": "受阻",
}

#: How an AI parameter reaches the business operation.
#: * ``body``      — same-named field of the declared Pydantic request model
#: * ``path``      — path parameter of the declared route
#: * ``multipart`` — multipart/form field (no Pydantic body)
#: * ``executor``  — the original service/executor consumes it directly
#: * ``query``     — query parameter of the declared route
PARAM_BINDINGS = ("body", "path", "multipart", "executor", "query")


def canonical(value: Any) -> str:
    """Stable JSON: same declarations always produce the same bytes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str = "string"
    required: bool = True
    binding: str = "body"
    max_length: int | None = None
    enum: tuple[str, ...] = ()
    description: str = ""
    #: The original executor derives this value (for example the current script
    #: basis), so an explicit adapter contract may keep it optional even when
    #: the HTTP request model marks it required.
    derive: bool = False

    def contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "binding": self.binding,
            "max_length": self.max_length,
            "enum": list(self.enum),
        }


@dataclass(frozen=True)
class CapabilityDeclaration:
    """One reviewed, delegateable operation of the real product."""

    action_id: str
    feature_id: str
    module: str
    label: str
    description: str
    params: tuple[ParamSpec, ...]
    api_route: str | None
    request_schema: str | None
    response_schema: str | None
    executor: str | None
    authority: str
    permission_tier: str
    project_scope: str = "PROJECT"
    preconditions: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()
    confirmation_policy: str = "预览影响后由用户明确确认。"
    occupancy_gate: str = "FULL"
    gpu: bool = False
    paid: bool = False
    cost_note: str = ""
    idempotency_policy: str = "request_key + attempt_no 保证同一次请求只执行一次。"
    readback_contract: str = "读取原业务台账/回读接口。"
    rollback_policy: str = "无自动回滚；按原业务操作人工恢复。"
    availability: str = "AVAILABLE"
    dispatch: str | None = None
    executor_bound: bool = True
    introduced_version: str = APP_VERSION
    contract_revision: int = 2
    examples: tuple[str, ...] = ()
    note: str = ""
    page: str = "导演模式"
    status: str = "GUARDED"
    reversible: str = "NO"

    #: Fields that define the *contract*, not the prose.  A description edit
    #: must not invalidate a person's saved authorization; a parameter, tier or
    #: executor change must.
    def contract(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "module": self.module,
            "params": [param.contract() for param in self.params],
            "api_route": self.api_route,
            "request_schema": self.request_schema,
            "executor": self.executor,
            "authority": self.authority,
            "permission_tier": self.permission_tier,
            "project_scope": self.project_scope,
            "preconditions": list(self.preconditions),
            "gates": list(self.gates),
            "confirmation_policy": self.confirmation_policy,
            "occupancy_gate": self.occupancy_gate,
            "resource_cost": {"gpu": self.gpu, "paid": self.paid},
            "idempotency_policy": self.idempotency_policy,
            "readback_contract": self.readback_contract,
            "rollback_policy": self.rollback_policy,
            "availability": self.availability,
            "dispatch": self.dispatch,
            "executor_bound": self.executor_bound,
            "contract_revision": self.contract_revision,
        }

    def contract_digest(self) -> str:
        return digest(self.contract())

    def param_names(self) -> tuple[str, ...]:
        return tuple(param.name for param in self.params)


@dataclass(frozen=True)
class ReadCapabilityDeclaration:
    action_id: str
    feature_id: str
    module: str
    label: str
    page: str
    executor: str | None
    params: tuple[str, ...]
    readback: str
    note: str = ""
    api_route: str | None = None

    def contract(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "params": list(self.params),
            "api_route": self.api_route,
            "executor": self.executor,
            "readback": self.readback,
        }

    def contract_digest(self) -> str:
        return digest(self.contract())


@dataclass(frozen=True)
class FeatureDeclaration:
    """One product feature, as the product itself describes it.

    ``status`` must come from reviewed engineering evidence.  A route existing
    is not IMPLEMENTED; an accepted design document is not IMPLEMENTED either.
    """

    feature_id: str
    module: str
    title: str
    status: str
    description: str
    sources: tuple[str, ...] = ()
    apis: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    verification: str = "AUTOMATED"
    tags: tuple[str, ...] = ()
    not_supported: str | None = None
    docs: tuple[str, ...] = field(default_factory=tuple)
    knowledge_revision: str = "r1"

    def content(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "module": self.module,
            "title": self.title,
            "status": self.status,
            "description": self.description,
            "sources": list(self.sources),
            "apis": list(self.apis),
            "evidence": list(self.evidence),
            "verification": self.verification,
            "tags": list(self.tags),
            "not_supported": self.not_supported,
            "docs": list(self.docs),
        }

    def content_hash(self) -> str:
        return digest(self.content())


@dataclass(frozen=True)
class HumanOperation:
    operation: str
    api: str
    reason: str
    status: str


@dataclass(frozen=True)
class NotSupportedOperation:
    operation: str
    api: str
    reason: str


#: Feature ids that are allowed to appear in a capability declaration.  The
#: build check rejects a capability pointing at an undeclared feature, so the
#: knowledge index and the registry cannot drift apart silently.
def feature_index(features: tuple[FeatureDeclaration, ...]) -> dict[str, FeatureDeclaration]:
    return {feature.feature_id: feature for feature in features}

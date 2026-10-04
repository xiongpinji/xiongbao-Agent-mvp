"""Server-created personal MCP actor scopes and immutable model exposure receipts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from octop.infra.errors import ErrorCode, OctopError


@dataclass(frozen=True, slots=True)
class TrustedMCPActor:
    """Resolved by authenticated server entry points, never tool args or graph config."""

    user_id: int
    agent_id: str
    thread_id: str
    session_key: str
    source: str
    allowed_personal_servers: frozenset[str]
    locale: str

    def __post_init__(self) -> None:
        if type(self.user_id) is not int or self.user_id <= 0:
            raise ValueError("trusted MCP actor requires a positive user ID")
        for value in (self.agent_id, self.thread_id, self.session_key, self.source):
            if not value or value != value.strip():
                raise ValueError("trusted MCP actor requires resolved identity fields")
        if not isinstance(self.allowed_personal_servers, frozenset) or any(
            not name or name != name.strip() for name in self.allowed_personal_servers
        ):
            raise ValueError("trusted MCP actor requires immutable resolved server names")
        if self.locale not in {"zh", "en"}:
            raise ValueError("trusted MCP actor requires a resolved locale")


@dataclass(frozen=True, slots=True)
class PersonalMCPDescriptorReceipt:
    """Only identifiers and fingerprints; no private schema, credentials or body."""

    tool_name: str
    server_name: str
    connection_fingerprint: str
    descriptor_fingerprint: str


@dataclass(frozen=True, slots=True)
class PersonalMCPReceipt:
    """A model exposure snapshot; persistence/restore must remain server-owned."""

    actor: TrustedMCPActor
    descriptors: tuple[PersonalMCPDescriptorReceipt, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.descriptors, tuple):
            raise ValueError("personal MCP receipt descriptors must be immutable")


def personal_mcp_denied(reason: str, *, locale: str = "en") -> OctopError:
    """Use the existing localized permission envelope and a non-sensitive reason."""
    return OctopError.localized(ErrorCode.FORBIDDEN, locale, details={"reason": reason})


def _validate_receipt(actor: TrustedMCPActor, receipt: PersonalMCPReceipt) -> None:
    if receipt.actor != actor:
        raise personal_mcp_denied("personal_mcp_receipt_binding", locale=actor.locale)
    names: set[str] = set()
    for descriptor in receipt.descriptors:
        if (
            not descriptor.tool_name
            or descriptor.tool_name in names
            or descriptor.server_name not in actor.allowed_personal_servers
            or not descriptor.connection_fingerprint
            or not descriptor.descriptor_fingerprint
        ):
            raise personal_mcp_denied("personal_mcp_receipt_invalid", locale=actor.locale)
        names.add(descriptor.tool_name)


class MCPActorScope:
    """One invocation lease; invalidation also reaches tasks inheriting its reference."""

    __slots__ = ("_actor", "_active", "_receipt")

    def __init__(self, actor: TrustedMCPActor, receipt: PersonalMCPReceipt | None) -> None:
        if receipt is not None:
            _validate_receipt(actor, receipt)
        self._actor = actor
        self._active = True
        self._receipt = receipt

    @property
    def actor(self) -> TrustedMCPActor:
        return self._actor

    @property
    def active(self) -> bool:
        return self._active

    def require_active(self) -> None:
        if not self._active or _ACTOR_SCOPE.get() is not self:
            raise personal_mcp_denied("personal_mcp_scope_inactive", locale=self.actor.locale)

    @property
    def receipt(self) -> PersonalMCPReceipt | None:
        self.require_active()
        return self._receipt

    def record_model_receipt(self, receipt: PersonalMCPReceipt) -> None:
        self.require_active()
        _validate_receipt(self.actor, receipt)
        self._receipt = receipt

    def clear_model_receipt(self, expected: PersonalMCPReceipt) -> None:
        if self._receipt is expected:
            self._receipt = None

    def close(self) -> None:
        self._active = False
        self._receipt = None


_ACTOR_SCOPE: ContextVar[MCPActorScope | None] = ContextVar(
    "octop_personal_mcp_actor", default=None
)


def current_mcp_actor_scope() -> MCPActorScope | None:
    scope = _ACTOR_SCOPE.get()
    return scope if scope is not None and scope.active else None


def require_mcp_actor_scope() -> MCPActorScope:
    scope = _ACTOR_SCOPE.get()
    if scope is None:
        raise personal_mcp_denied("personal_mcp_scope_missing")
    scope.require_active()
    return scope


def export_personal_mcp_receipt() -> PersonalMCPReceipt:
    scope = require_mcp_actor_scope()
    receipt = scope.receipt
    if receipt is None:
        raise personal_mcp_denied("personal_mcp_receipt_missing", locale=scope.actor.locale)
    return receipt


@contextmanager
def trusted_mcp_actor_scope(
    actor: TrustedMCPActor,
    *,
    receipt: PersonalMCPReceipt | None = None,
) -> Iterator[MCPActorScope]:
    """Root may restore only a previous server-owned, authenticated pending receipt."""
    scope = MCPActorScope(actor, receipt)
    token = _ACTOR_SCOPE.set(scope)
    try:
        yield scope
    finally:
        scope.close()
        _ACTOR_SCOPE.reset(token)


@contextmanager
def without_trusted_mcp_actor_scope() -> Iterator[None]:
    """Unscoped nested entries must not inherit their caller's actor authority."""
    token = _ACTOR_SCOPE.set(None)
    try:
        yield
    finally:
        _ACTOR_SCOPE.reset(token)

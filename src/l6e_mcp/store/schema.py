"""Shared schema constants for l6e MCP local persistence."""
from __future__ import annotations

ACCOUNTING_MODE_ESTIMATE_ONLY = "estimate_only"
ACCOUNTING_MODE_EXACT_OPTIONAL = "exact_optional"
ACCOUNTING_MODE_EXACT_REQUIRED = "exact_required"
VALID_ACCOUNTING_MODES: frozenset[str] = frozenset({
    ACCOUNTING_MODE_ESTIMATE_ONLY,
    ACCOUNTING_MODE_EXACT_OPTIONAL,
    ACCOUNTING_MODE_EXACT_REQUIRED,
})

USAGE_CHANNEL_NONE = "none"
USAGE_CHANNEL_HOSTED_EDGE = "hosted_edge"
USAGE_CHANNEL_SELF_HOSTED_RELAY = "self_hosted_relay"
USAGE_CHANNEL_MANUAL_IMPORT = "manual_import"
VALID_USAGE_CHANNELS: frozenset[str] = frozenset({
    USAGE_CHANNEL_NONE,
    USAGE_CHANNEL_HOSTED_EDGE,
    USAGE_CHANNEL_SELF_HOSTED_RELAY,
    USAGE_CHANNEL_MANUAL_IMPORT,
})

ACTOR_TYPE_PARENT_AGENT = "parent_agent"
ACTOR_TYPE_SUBAGENT = "subagent"
VALID_ACTOR_TYPES: frozenset[str] = frozenset({
    ACTOR_TYPE_PARENT_AGENT,
    ACTOR_TYPE_SUBAGENT,
})

CALL_MODE_ASK = "ask"
CALL_MODE_PLAN = "plan"
CALL_MODE_AGENT = "agent"
VALID_CALL_MODES: frozenset[str] = frozenset({
    CALL_MODE_ASK,
    CALL_MODE_PLAN,
    CALL_MODE_AGENT,
})

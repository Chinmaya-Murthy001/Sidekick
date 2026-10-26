from __future__ import annotations

import logging
import os

from .rule_agent import RuleAgent

log = logging.getLogger(__name__)


def make_agent(kind: str | None = None):
    """Pick the agent: Claude when credentials are available, else the offline planner."""
    kind = (kind or os.getenv("SIDEKICK_AGENT", "auto")).lower()
    if kind == "rules":
        return RuleAgent()
    if kind in {"claude", "auto"}:
        has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        if has_key or kind == "claude":
            from .claude_agent import ClaudeAgent
            return ClaudeAgent()
    log.info("No Anthropic credentials found; using the offline rule-based agent")
    return RuleAgent()

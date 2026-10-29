from __future__ import annotations

import logging
import os

from .rule_agent import RuleAgent

log = logging.getLogger(__name__)


def make_agent(kind: str | None = None):
    """Pick the agent: OpenRouter or Claude when a key is set, else the offline planner.

    SIDEKICK_AGENT can force one: auto (default), openrouter, claude or rules.
    """
    kind = (kind or os.getenv("SIDEKICK_AGENT") or "auto").lower()
    if kind == "rules":
        return RuleAgent()
    if kind == "openrouter" or (kind == "auto" and os.getenv("OPENROUTER_API_KEY")):
        from .openrouter_agent import OpenRouterAgent
        return OpenRouterAgent()
    if kind == "claude" or (kind == "auto" and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))):
        from .claude_agent import ClaudeAgent
        return ClaudeAgent()
    log.info("No AI key found; using the offline rule-based agent")
    return RuleAgent()


def agent_label(agent) -> str:
    """Short name for the UI, e.g. 'Claude Sonnet 5.5 via OpenRouter'."""
    if agent.name == "openrouter":
        model = agent.model.split("/")[-1].removeprefix("claude-").replace("-", " ")
        return f"{model.title()} · OpenRouter"
    if agent.name == "claude":
        return agent.model
    return "Offline planner"

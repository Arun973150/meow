"""What a tool did, recorded.

Its own module so that both the harness and the tool modules can import it
without importing each other. It lived in `harness.py`, and once the tools
moved out they needed it back - which would have made harness import tools and
tools import harness. A shared type belongs below both, not inside one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..desktop.actions import Outcome, Target


@dataclass
class ToolRun:
    """One tool call and what came of it. Kept for the evaluation."""

    tool: str
    argument: str
    outcome: Outcome
    target: Target | None = None

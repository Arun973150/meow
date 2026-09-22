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
    # True, False, or None for could-not-tell - the same three-way verdict
    # `verify` produces, recorded rather than only spoken.
    #
    # It used to live solely in the string handed back to the model, so a step
    # the verifier had positively determined DID NOT HAPPEN was invisible to
    # the planner: last_error stayed clear, nothing was refused, and the step
    # was marked DONE. A four-step plan ran to the end reporting "nothing
    # changed visibly" three times and then described what it had achieved.
    #
    # None must stay non-failing. Clicking into a text box changes nothing
    # observable and that is not failure - a verifier that treats unknown as
    # no is as useless as one that treats it as yes.
    verified: bool | None = None

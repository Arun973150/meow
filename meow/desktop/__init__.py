"""Seeing the machine, and operating it.

The core thesis lives here. `uia` reads the accessibility tree, `actions`
presses through it, and `grounding` keeps the UIA / vision / hybrid split
swappable because the evaluation depends on it. `vision` is kept honest by
the same measurement: it scored 0/30 against UIA's 30/30 at locating a
control, so pixels are a fallback and a baseline, never the plan.
"""

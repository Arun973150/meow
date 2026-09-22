"""The parts that decide.

`router` classifies, `risk` decides whether to ask, `planner` breaks work
into steps, `harness` runs the tools, and `memory` is the one place any of
them remembers anything. `evaluation` is the ablation that says the
grounding choice was measured rather than assumed.
"""

"""What the agent can do, grouped by what it touches.

One module per concern, each exposing `build(harness) -> list`. Adding a
capability is adding a module and a line in ORDER, not editing a class - which
is the whole point. All thirty of these were defined inline inside
`Harness.__init__`, making it 1,100 lines and meaning every new tool, however
unrelated, was a diff to the same function.

ORDER is the order the model sees them in, and it is not alphabetical:
desktop first because it is what the cat is for, then the accounts, then
looking things up, then the things it makes.
"""

from . import connectors, desktop, knowledge, teaching, workspace

ORDER = (desktop, teaching, connectors, knowledge, workspace)


def build(harness) -> list:
    """Every tool, bound to one harness."""
    tools = []
    for module in ORDER:
        tools.extend(module.build(harness))
    return tools

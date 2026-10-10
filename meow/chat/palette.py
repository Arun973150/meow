"""One palette and one type stack, for every surface in the chat window.

The colours were spread across two files - a stylesheet in `window.py` and a
dict in `bubbles.py` - in two different greys, because a Qt stylesheet cannot
reach a delegate that paints its own pixels. So a bubble and the list it sits
in were the same colour by coincidence and drifted the moment either changed.
One module, imported by both.

**These are Apple's own system values, not approximations of them.** macOS
dark mode is a cool neutral grey with a blue accent; the window was a warm
brown-grey (#1b1a19) with amber and a slate blue, which reads as a terminal
theme rather than as a Mac application. The specific numbers below are the
macOS semantic colours - systemBlue, secondarySystemBackground,
tertiaryLabel - so the result matches a real Mac window rather than a
remembered one.

⚠ **SF Pro Display IS installed on this machine and must not be assumed
anywhere else.** Checked, not hoped: `QFontDatabase.families()` has "SF Pro
Display" and not "SF Pro Text". So the stack falls through SF Pro Text (for
machines that have the proper body cut), then Segoe UI Variable Text, which
is Windows 11's own variable face and the closest thing to SF at body sizes,
then plain Segoe UI.

And Display is a DISPLAY cut - Apple designs it for 20pt and up, with tighter
spacing than the text cut. Using it for 13px body would be the wrong half of
the family, so headings get Display and body gets the text stack.
"""

from __future__ import annotations

# --- macOS dark mode -------------------------------------------------------
#
# Translucency is deliberately NOT attempted. macOS sidebars are vibrant
# because the compositor blurs what is behind them, and Qt on Windows cannot
# do that without a per-platform blur-behind hack that breaks on every
# version. A flat approximation of vibrancy looks like a Mac; a broken one
# looks like a bug.

WINDOW = "#1e1e1e"        # windowBackgroundColor
SIDEBAR = "#252527"       # the sidebar sits very slightly above the window
SURFACE = "#2c2c2e"       # secondarySystemBackground
RAISED = "#3a3a3c"        # tertiarySystemBackground - incoming bubbles, fields
PRESSED = "#48484a"

# Separators are HAIRLINES at low opacity, never a solid 1px grey. A visible
# border is the single most Windows-looking thing in a dark interface.
HAIRLINE = "rgba(255, 255, 255, 0.08)"
HAIRLINE_STRONG = "rgba(255, 255, 255, 0.14)"

LABEL = "#ffffff"
LABEL_SECONDARY = "rgba(235, 235, 245, 0.62)"
LABEL_TERTIARY = "rgba(235, 235, 245, 0.32)"

ACCENT = "#0a84ff"        # systemBlue, dark appearance
ACCENT_BRIGHT = "#409cff"
AMBER = "#ff9f0a"         # systemOrange - the one row something waits on
GREEN = "#30d158"         # systemGreen - a file that now exists
ON_ACCENT = "#ffffff"

# --- type ------------------------------------------------------------------

DISPLAY_STACK = ('"SF Pro Display", "Segoe UI Variable Display", '
                 '"Segoe UI", sans-serif')
TEXT_STACK = ('"SF Pro Text", "SF Pro Display", "Segoe UI Variable Text", '
              '"Segoe UI", sans-serif')

# For QFont, which takes a list rather than a CSS string. Qt walks these in
# order, so the same fallback applies to the painted bubbles as to the
# stylesheet - otherwise the list and the bubbles inside it use two faces.
DISPLAY_FAMILIES = ["SF Pro Display", "Segoe UI Variable Display", "Segoe UI"]
TEXT_FAMILIES = ["SF Pro Text", "SF Pro Display",
                 "Segoe UI Variable Text", "Segoe UI"]

BODY_POINTS = 10
NAME_POINTS = 8

# --- shape -----------------------------------------------------------------
#
# Apple's radii are larger and more consistent than Windows'. A Messages
# bubble is effectively a pill; a sidebar row is a soft rounded rect inset
# from the edge rather than a full-width highlight.

BUBBLE_RADIUS = 18
CARD_RADIUS = 10
ROW_RADIUS = 7
FIELD_RADIUS = 10
BUTTON_RADIUS = 7


def font(families, points: int, weight=None):
    """A QFont with the whole fallback stack, not just the first name."""
    from PySide6.QtGui import QFont

    made = QFont()
    made.setFamilies(list(families))
    made.setPointSize(points)
    if weight is not None:
        made.setWeight(weight)
    return made

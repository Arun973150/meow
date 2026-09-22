"""Chat bubbles, painted rather than marked up.

The first attempt rendered messages as HTML into a `QTextBrowser`, which is the
obvious thing and looks wrong. Qt's rich text is a subset: `border-radius` does
nothing, and a `div` with a background colour stretches the full width of the
viewport, so every message came out as a flat grey bar edge to edge. It read as
a log file, not a conversation.

So each message is a list row with a delegate that paints it: a rounded
rectangle sized to its own text, the user's on the right and everyone else's on
the left, with the speaker's name above in small dim type.

Painting also fixes the thing HTML made hard - a bubble that is as wide as its
content up to a limit, rather than as wide as the window. Width is meaning
here: a three-word answer should look like a three-word answer.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate

# Roles on each list item. Kept here so the window and the delegate agree
# without passing a dataclass through Qt's variant system.
WHO = Qt.UserRole + 1
TEXT = Qt.UserRole + 2

# A bubble never exceeds this share of the width. Full-width text is hard to
# read and stops the conversation looking like one.
MAX_WIDTH_SHARE = 0.72

PADDING_X = 13
PADDING_Y = 9
NAME_HEIGHT = 15
GAP = 12
CORNER = 11

# Who is speaking decides the colour. Three cases rather than two: the user,
# the cat, and a background agent - which is a different voice again and
# should not be mistaken for the cat answering.
COLOURS = {
    "user": ("#2f3b44", "#dceaf5", "#7fa8c9"),
    "meow": ("#2a2927", "#e8e6e3", "#d8a76b"),
    # A file the agent wrote. Its own colour because it is not
    # something anyone said - it is a thing that now exists, and
    # unlike every other row it can be pressed.
    "file": ("#2b332b", "#cfe6cd", "#7bc96f"),
}
AGENT_COLOURS = ("#242a24", "#d7e4d5", "#7bc96f")


def _palette(who: str) -> tuple[str, str, str]:
    return COLOURS.get(who, AGENT_COLOURS)


def _name_of(who: str) -> str:
    return {"user": "you", "meow": "meow",
            "file": "made this - click to open"}.get(who, who)


def is_file(index) -> bool:
    """Is this row a produced file rather than something said?"""
    return str(index.data(WHO) or "") == "file"


def file_path(index) -> str:
    return str(index.data(TEXT) or "")


class MessageDelegate(QStyledItemDelegate):
    """Paints one message as a bubble sized to its own text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.body_font = QFont("Segoe UI", 10)
        self.name_font = QFont("Segoe UI", 8)

    def _wrap_width(self, option) -> int:
        return max(160, int(option.rect.width() * MAX_WIDTH_SHARE) - PADDING_X * 2)

    def _text_rect(self, text: str, width: int) -> QRect:
        metrics = QFontMetrics(self.body_font)
        return metrics.boundingRect(QRect(0, 0, width, 10_000),
                                    Qt.TextWordWrap, text)

    def _shown(self, index) -> str:
        """The text as it will actually be painted.

        sizeHint and paint MUST agree. They did not: a file row measured its
        full path and painted only the name, so the row was sized for text
        twice as long as what appeared in it.
        """
        text = str(index.data(TEXT) or "")
        if str(index.data(WHO) or "") == "file":
            return text.replace(chr(92), "/").rsplit("/", 1)[-1]
        return text

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt's name
        text = self._shown(index)
        bounds = self._text_rect(text, self._wrap_width(option))
        height = bounds.height() + PADDING_Y * 2 + NAME_HEIGHT + GAP
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option, index) -> None:
        who = str(index.data(WHO) or "meow")
        text = self._shown(index)
        background, foreground, name_colour = _palette(who)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        wrap_width = self._wrap_width(option)
        bounds = self._text_rect(text, wrap_width)
        bubble_width = bounds.width() + PADDING_X * 2
        bubble_height = bounds.height() + PADDING_Y * 2

        area = option.rect
        mine = who == "user"
        left = (area.right() - bubble_width - 14) if mine else (area.left() + 14)
        top = area.top() + NAME_HEIGHT

        # The speaker, above the bubble and aligned with it. Small and dim:
        # it is there to be glanced at, not read.
        painter.setFont(self.name_font)
        painter.setPen(QPen(QColor(name_colour)))
        name_rect = QRect(left, area.top(), bubble_width, NAME_HEIGHT)
        painter.drawText(name_rect,
                         Qt.AlignVCenter | (Qt.AlignRight if mine else Qt.AlignLeft),
                         _name_of(who))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(
            QRectF(left, top, bubble_width, bubble_height), CORNER, CORNER)

        painter.setFont(self.body_font)
        painter.setPen(QPen(QColor(foreground)))
        painter.drawText(
            QRect(left + PADDING_X, top + PADDING_Y, wrap_width, bounds.height()),
            Qt.TextWordWrap, text)

        if who == "file":
            # Underlined, so it reads as something to press rather
            # than something to read.
            painter.setPen(QPen(QColor(foreground), 1))
            baseline = top + bubble_height - 7
            painter.drawLine(left + PADDING_X, baseline,
                             left + PADDING_X + bounds.width(),
                             baseline)

        painter.restore()

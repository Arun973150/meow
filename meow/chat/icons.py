"""Icons, drawn rather than shipped.

The cat is drawn procedurally in `meow/cat/sprite.py` for a reason - an
expression is a number, so states can cross-fade and the gaze can aim anywhere.
The same argument is weaker here but the practical ones are not: no binary
files in the repository, no DPI-specific sizes to keep in step, and a running
agent can be drawn mid-spin without a sprite sheet.

Each kind of conversation gets its own mark, because the point of the sidebar
is telling them apart at a glance rather than reading six similar titles.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Enough that the mark is legible at sidebar size and still crisp when Windows
# scales a tray icon up on a high-DPI display.
ICON_SIZE = 64

INK = QColor("#e8e6e3")
DIM = QColor("#8b8985")
LIVE = QColor("#7bc96f")      # something is still running
TASK = QColor("#6ba4d8")
CHAT = QColor("#d8a76b")


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    return pixmap, painter


def cat_icon(colour: QColor = CHAT) -> QIcon:
    """The line-art cat head - the same shape as the overlay, simplified."""
    pixmap, painter = _canvas()
    pen = QPen(colour, 4.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)

    # Head: a rounded triangle-ish face, wider than tall.
    face = QPainterPath()
    face.moveTo(14, 26)
    face.lineTo(20, 12)          # left ear
    face.lineTo(27, 22)
    face.lineTo(37, 22)
    face.lineTo(44, 12)          # right ear
    face.lineTo(50, 26)
    face.cubicTo(54, 44, 42, 54, 32, 54)
    face.cubicTo(22, 54, 10, 44, 14, 26)
    painter.drawPath(face)

    # Eyes, closed and content, which is how the cat sits at rest.
    painter.drawArc(QRectF(20, 30, 10, 8), 0, 180 * 16)
    painter.drawArc(QRectF(34, 30, 10, 8), 0, 180 * 16)
    # Nose.
    painter.drawLine(QPointF(32, 40), QPointF(32, 43))
    painter.end()
    return QIcon(pixmap)


def magnifier_icon(colour: QColor = TASK) -> QIcon:
    """Research: a task that is out looking something up."""
    pixmap, painter = _canvas()
    pen = QPen(colour, 5.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawEllipse(QRectF(14, 14, 28, 28))
    painter.drawLine(QPointF(38, 38), QPointF(50, 50))
    painter.end()
    return QIcon(pixmap)


def gear_icon(colour: QColor = TASK) -> QIcon:
    """A plan: steps being worked through."""
    pixmap, painter = _canvas()
    painter.setPen(QPen(colour, 4.5))
    painter.drawEllipse(QRectF(22, 22, 20, 20))
    # Eight teeth, which reads as a gear without drawing a real involute.
    painter.save()
    painter.translate(32, 32)
    for _ in range(8):
        painter.drawLine(QPointF(0, -15), QPointF(0, -21))
        painter.rotate(45)
    painter.restore()
    painter.end()
    return QIcon(pixmap)


def dot_icon(colour: QColor) -> QIcon:
    """A plain status dot, for the running indicator beside a title."""
    pixmap, painter = _canvas()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(colour))
    painter.drawEllipse(QRectF(20, 20, 24, 24))
    painter.end()
    return QIcon(pixmap)


# Named so the store can record which icon a conversation wants without the
# window needing to know what kinds exist.
BY_NAME = {
    "cat": cat_icon,
    "magnifier": magnifier_icon,
    "gear": gear_icon,
}


def for_conversation(icon: str, live: bool) -> QIcon:
    """The icon for one sidebar row.

    A live conversation is drawn in the running colour rather than given a
    second badge - one mark carrying both facts is easier to scan than two
    marks side by side, and the sidebar is meant to be read at a glance.
    """
    draw = BY_NAME.get(icon, cat_icon)
    return draw(LIVE if live else DIM)

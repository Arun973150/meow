"""The window a handed-over task gets.

Small, in the corner, stacked when there is more than one. It shows the goal,
what has happened so far, and whether anything is queued behind the current
step.

Click-through like everything else Meow draws. A panel that could take focus
would steal it from whatever the user went back to doing, which defeats the
point of handing the work over in the first place.

Excluded from capture too, for the same reason the cat is: the model
screenshots the desktop, and a window describing what the model is doing is a
confusing thing for it to read back.

The status dot carries the state rather than a word, because a word wide enough
to say "running" is a word wide enough to push the title out of the window.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .cat.bubble import _load_font
from .tasks import Task, TaskState

# Wide enough to read a finding in. 280 was chosen to be unobtrusive and was
# unobtrusive to the point of useless - a research result trimmed to 256px is
# a source name and nothing else, and the user could not see what had been
# found.
WIDTH = 430
PADDING = 12
LINE_HEIGHT = 16
TITLE_HEIGHT = 22
MAX_VISIBLE_LINES = 12
CORNER = 10

# Long lines wrap rather than being cut. A finding is a sentence, and half a
# sentence with an ellipsis is not a result.
WRAP_LINES = 2

# Gap between stacked windows, and from the screen edge.
STACK_GAP = 8
EDGE_MARGIN = 20


@dataclass(frozen=True)
class PanelPalette:
    fill: tuple[int, int, int, int] = (252, 252, 254, 246)
    border: tuple[int, int, int, int] = (26, 26, 30, 70)
    title: tuple[int, int, int, int] = (26, 26, 30, 255)
    body: tuple[int, int, int, int] = (92, 92, 104, 255)
    queued: tuple[int, int, int, int] = (150, 110, 40, 255)
    error: tuple[int, int, int, int] = (170, 55, 55, 255)

    # One dot, four meanings. Cheaper in pixels than a word, and readable at a
    # glance from across a screen, which a word is not.
    running: tuple[int, int, int, int] = (70, 140, 230, 255)
    done: tuple[int, int, int, int] = (70, 160, 90, 255)
    failed: tuple[int, int, int, int] = (190, 70, 70, 255)
    stopped: tuple[int, int, int, int] = (140, 140, 150, 255)

    @classmethod
    def for_background(cls, luminance: float) -> "PanelPalette":
        if luminance < 0.42:
            return cls(
                fill=(30, 30, 34, 246),
                border=(236, 236, 240, 60),
                title=(240, 240, 244, 255),
                body=(168, 168, 180, 255),
                queued=(220, 180, 90, 255),
                error=(230, 110, 110, 255),
            )
        return cls()


class TaskPanel:
    """Draws one task as a small card."""

    def __init__(self, palette: PanelPalette | None = None) -> None:
        self.palette = palette or PanelPalette()
        self.title_font = _load_font(12)
        self.body_font = _load_font(11)

    def _wrap(self, text: str, draw, font) -> list[str]:
        """Break a line to fit the panel, up to WRAP_LINES rows."""
        limit = WIDTH - PADDING * 2
        if draw.textlength(text, font=font) <= limit:
            return [text]

        rows, current = [], ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= limit:
                current = candidate
                continue
            rows.append(current)
            current = word
            if len(rows) == WRAP_LINES:
                break
        if current and len(rows) < WRAP_LINES:
            rows.append(current)

        if len(rows) == WRAP_LINES:
            last = rows[-1]
            while (draw.textlength(last + "…", font=font) > limit
                   and len(last) > 4):
                last = last[:-2]
            rows[-1] = last.rstrip() + "…"
        return rows or [text[:40]]

    def rows_for(self, task: Task) -> list[tuple[str, str]]:
        """Every visible row as (text, kind), already wrapped."""
        from PIL import Image as _Image
        measuring = ImageDraw.Draw(_Image.new("RGBA", (1, 1)))

        rows: list[tuple[str, str]] = []
        for line in task.lines():
            for piece in self._wrap(line.text, measuring, self.body_font):
                rows.append((piece, line.kind))
        # Newest at the bottom, so it reads like a log.
        return rows[-MAX_VISIBLE_LINES:]

    def height(self, task: Task) -> int:
        shown = max(1, len(self.rows_for(task)))
        return PADDING * 2 + TITLE_HEIGHT + shown * LINE_HEIGHT

    def _dot_colour(self, task: Task):
        return {
            TaskState.RUNNING: self.palette.running,
            TaskState.DONE: self.palette.done,
            TaskState.FAILED: self.palette.failed,
            TaskState.STOPPED: self.palette.stopped,
        }[task.state]

    def render(self, task: Task, phase: float = 0.0) -> Image.Image:
        height = self.height(task)
        image = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle([0, 0, WIDTH - 1, height - 1], radius=CORNER,
                               fill=self.palette.fill,
                               outline=self.palette.border, width=1)

        # The dot breathes while running and sits still when finished, so a
        # glance answers "is it still going?" without reading anything.
        import math
        radius = 4.0
        if task.state is TaskState.RUNNING:
            radius = 3.4 + 1.2 * (0.5 + 0.5 * math.sin(phase * 3.4))
        centre_x, centre_y = PADDING + 4, PADDING + 6
        draw.ellipse([centre_x - radius, centre_y - radius,
                      centre_x + radius, centre_y + radius],
                     fill=self._dot_colour(task))

        elapsed = f"{task.seconds:.0f}s"
        draw.text((PADDING + 14, PADDING), task.title,
                  font=self.title_font, fill=self.palette.title)
        draw.text((WIDTH - PADDING - 26, PADDING), elapsed,
                  font=self.body_font, fill=self.palette.body)

        # The most recent lines, oldest first, so it reads like a log rather
        # than like a stack.
        y = PADDING + TITLE_HEIGHT
        for text, kind in self.rows_for(task):
            colour = {
                "error": self.palette.error,
                "queued": self.palette.queued,
            }.get(kind, self.palette.body)
            draw.text((PADDING, y), text, font=self.body_font, fill=colour)
            y += LINE_HEIGHT

        return image


def stack_positions(tasks: list[Task], panel: TaskPanel,
                    monitor) -> list[tuple[Task, int, int]]:
    """Where each window goes: a column up the right edge.

    Newest at the bottom, nearest the cat, because that is the one being
    talked about. Growing upward keeps the others where they were instead of
    sliding every window every time one appears.
    """
    placed: list[tuple[Task, int, int]] = []
    left = monitor.work_right - WIDTH - EDGE_MARGIN
    bottom = monitor.work_bottom - EDGE_MARGIN - 120  # clear of the cat's home

    for task in reversed(tasks):
        height = panel.height(task)
        bottom -= height
        placed.append((task, left, bottom))
        bottom -= STACK_GAP
    return placed

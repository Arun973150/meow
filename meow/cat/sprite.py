"""The cat.

Minimal line art: ears, eyes, nose, whiskers. No body, no fill, no colour.

Proportions are traced from the reference drawing in image.png. The geometry
below is that drawing normalised into a 0..1 box, which is why the numbers look
arbitrary - they are measured, not chosen.

Drawn procedurally rather than loaded as a PNG, for two reasons. Every
expression is a set of numbers, so the cat blends smoothly between states
instead of snapping between frames, and it can look in an arbitrary direction
when it points at something on screen. A sprite sheet would need a frame per
angle. It also stays sharp at any size and any display scale, which a bitmap
does not.

Every stroke is drawn twice: a wider halo underneath, then the ink on top. The
overlay floats over whatever the user has open, and single-colour line art
disappears against a background of its own shade. Subtitles carry outlines for
the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

# Measured, not guessed. Cost scales with the square: at 2x a frame costs about
# a quarter of what it does at 4x, and rendered side by side at 3x zoom the two
# are indistinguishable - including on the ear tips, which is where an earlier
# version of this comment claimed 2x broke down. It did not.
SUPERSAMPLE = 2

# The reference face is far wider than it is tall. A square sprite would be
# mostly empty space above and below it.
ASPECT = 0.70

STROKE_WIDTH = 0.030  # fraction of sprite width


@dataclass(frozen=True)
class CatPalette:
    ink: tuple[int, int, int] = (26, 26, 30)
    # Semi-transparent, so the halo reads as separation from the background
    # rather than a white outline drawn around the cat.
    halo: tuple[int, int, int, int] = (255, 255, 255, 165)

    @classmethod
    def for_background(cls, luminance: float) -> "CatPalette":
        """Pick ink that contrasts with whatever the cat is sitting on.

        Dark ink on a dark window is invisible, and no amount of halo tuning
        fixes it - a halo can outline a shape, but it cannot make the shape
        itself readable. Since the overlay floats over content we do not
        control, the only honest answer is to look at what is underneath and
        invert when it is dark.

        The threshold sits below the midpoint because a light cat on a
        mid-grey background reads better than a dark one.
        """
        if luminance < 0.42:
            return cls(ink=(244, 244, 248), halo=(0, 0, 0, 150))
        return cls(ink=(26, 26, 30), halo=(255, 255, 255, 165))


@dataclass(frozen=True)
class CatPose:
    """One frame of the cat, as numbers.

    Every state in animation.py is a function from time to one of these.
    Nothing else in the project needs to know how a cat is drawn.
    """

    # 0 = stubby ears, 1 = fully perked up
    ear_perk: float = 1.0
    # Shape of a line eye: +1 is a content peak, 0 is flat, -1 dips downward
    eye_curve: float = 1.0
    # Crossfades the line eye into a round open eye. 0 = line, 1 = open
    eye_dot: float = 0.0
    # Where open eyes look, -1..1 on each axis
    look_x: float = 0.0
    look_y: float = 0.0
    # 0 = no mouth drawn, 1 = wide open. Speech drives this
    mouth_open: float = 0.0
    # Degrees, positive tilts clockwise on screen
    head_tilt: float = 0.0
    # Vertical offset as a fraction of sprite height - breathing
    body_bob: float = 0.0
    # Horizontal offset, -1..1, for leaning toward something
    lean_x: float = 0.0
    # Whiskers lift when alert, droop when asleep
    whisker_lift: float = 0.0
    # Sleep marks fade in with this
    zzz: float = 0.0


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


class CatRenderer:
    """Draws a CatPose into RGBA pixels."""

    def __init__(self, width: int = 96, palette: CatPalette | None = None) -> None:
        self.width = width
        self.height = max(1, round(width * ASPECT))
        self.palette = palette or CatPalette()

    def render(self, pose: CatPose) -> Image.Image:
        """One frame, RGBA, at width x height."""
        canvas_width = self.width * SUPERSAMPLE
        canvas_height = self.height * SUPERSAMPLE
        image = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

        # The face goes on its own layer so head_tilt can rotate it as a unit.
        # Tilting each stroke separately would shear the face apart.
        face = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(face)

        offset_x = pose.lean_x * 0.045
        offset_y = pose.body_bob * 0.022

        def point(x: float, y: float) -> tuple[float, float]:
            return (x + offset_x) * canvas_width, (y + offset_y) * canvas_height

        core = max(1, round(canvas_width * STROKE_WIDTH))
        # Narrow enough to read as an edge on the ink rather than a second
        # outline. Wider than this and the cat looks hollow on a dark
        # background - two white lines with a gap, instead of one dark one.
        halo = core + max(2, round(canvas_width * 0.009))

        self._draw_head(draw, point, pose, core, halo)
        self._draw_eyes(draw, point, pose, core, halo)
        self._draw_nose_and_mouth(draw, point, pose, core, halo)
        self._draw_whiskers(draw, point, pose, core, halo)

        if abs(pose.head_tilt) > 0.1:
            face = face.rotate(
                # Pillow rotates counter-clockwise; head_tilt is clockwise on
                # screen, so the sign flips.
                -pose.head_tilt,
                resample=Image.BICUBIC,
                center=point(0.49, 0.52),
            )
        image = Image.alpha_composite(image, face)

        if pose.zzz > 0.01:
            # Floating above the cat, not part of it, so never rotated with it.
            self._draw_sleep_marks(ImageDraw.Draw(image), point, pose, core)

        return image.resize((self.width, self.height), Image.LANCZOS)

    # --- parts -----------------------------------------------------------

    def _draw_head(self, draw, point, pose, core, halo) -> None:
        """Ears and brow as a single open polyline.

        Down the left outer edge, up the inner edge, across the brow, up the
        right inner edge, down the right outer edge. One stroke, six points.
        """
        # Perked ears are taller: the tips rise while the brow stays put.
        tip_y = _lerp(0.33, 0.18, pose.ear_perk)

        self._stroke(
            draw,
            [
                point(0.172, 0.604),
                point(0.172, tip_y),
                point(0.340, 0.381),
                point(0.667, 0.381),
                point(0.811, tip_y),
                point(0.811, 0.604),
            ],
            core,
            halo,
        )

    def _draw_eyes(self, draw, point, pose, core, halo) -> None:
        """Line eyes and round eyes, crossfaded by eye_dot.

        Morphing a chevron into a circle geometrically looks wrong at every
        midpoint. Drawing both and fading between them does not, and at this
        size the overlap during the fade is invisible.
        """
        line_alpha = 1.0 - pose.eye_dot

        for center_x in (0.340, 0.647):
            if line_alpha > 0.01:
                apex_y = 0.6754 - 0.105 * pose.eye_curve
                self._stroke(
                    draw,
                    [
                        point(center_x - 0.060, 0.6754),
                        point(center_x, apex_y),
                        point(center_x + 0.060, 0.6754),
                    ],
                    core, halo, line_alpha,
                )

            if pose.eye_dot > 0.08:
                radius = 0.046 * pose.eye_dot
                shift_x = pose.look_x * 0.020
                shift_y = pose.look_y * 0.022
                left, top = point(
                    center_x + shift_x - radius,
                    0.625 + shift_y - radius / ASPECT,
                )
                right, bottom = point(
                    center_x + shift_x + radius,
                    0.625 + shift_y + radius / ASPECT,
                )
                # Filled with an outline rather than one ellipse inside
                # another. A blink shrinks this dot toward nothing, and an
                # inset inner box inverts before it gets there - Pillow then
                # raises "x1 must be greater than or equal to x0" mid-blink.
                draw.ellipse(
                    [left, top, right, bottom],
                    fill=self._faded(self.palette.ink, pose.eye_dot),
                    outline=self._faded(self.palette.halo, pose.eye_dot),
                    width=max(1, round(core * 0.6)),
                )

    def _draw_nose_and_mouth(self, draw, point, pose, core, halo) -> None:
        # A "T": a short bar with a stem dropping from its centre.
        self._stroke(draw, [point(0.449, 0.7365), point(0.538, 0.7365)],
                     core, halo)
        stem_bottom = 0.8549 - 0.028 * pose.mouth_open
        self._stroke(draw, [point(0.494, 0.7365), point(0.494, stem_bottom)],
                     core, halo)

        if pose.mouth_open > 0.02:
            # Opens downward from the stem, so the nose stays put and only the
            # mouth moves. Growing in both directions reads as the whole face
            # stretching.
            half_width = 0.045 + 0.028 * pose.mouth_open
            depth = 0.115 * pose.mouth_open
            self._stroke(
                draw,
                [
                    point(0.494 - half_width, stem_bottom),
                    point(0.494, stem_bottom + depth),
                    point(0.494 + half_width, stem_bottom),
                ],
                core, halo, min(1.0, pose.mouth_open * 2.5),
            )

    def _draw_whiskers(self, draw, point, pose, core, halo) -> None:
        # Two bars each side, clear of the head. The lift is deliberately
        # subtle - a supporting cue, not a state on its own.
        lift = pose.whisker_lift * 0.028
        for x_start, x_end in ((0.067, 0.209), (0.791, 0.935)):
            self._stroke(draw, [point(x_start, 0.6826 - lift),
                                point(x_end, 0.6826 - lift)], core, halo)
            self._stroke(draw, [point(x_start, 0.8262 + lift * 0.5),
                                point(x_end, 0.8262 + lift * 0.5)], core, halo)

    def _draw_sleep_marks(self, draw, point, pose, core) -> None:
        """Three sleep marks drifting up and right, fading as they rise."""
        for index in range(3):
            progress = index / 2.0
            alpha = pose.zzz * (1.0 - progress * 0.5)
            if alpha <= 0.01:
                continue
            size = _lerp(0.042, 0.070, progress)
            x = 0.855 + progress * 0.040
            y = 0.30 - progress * 0.110
            self._stroke(
                draw,
                [
                    point(x, y),
                    point(x + size, y),
                    point(x, y + size / ASPECT),
                    point(x + size, y + size / ASPECT),
                ],
                max(1, core - 1), max(2, core + 1), alpha,
            )

    # --- stroke helpers --------------------------------------------------

    def _stroke(self, draw, points, core: int, halo: int,
                amount: float = 1.0) -> None:
        """One polyline: haloed underneath, inked on top."""
        draw.line(points, fill=self._faded(self.palette.halo, amount),
                  width=halo, joint="curve")
        draw.line(points, fill=self._faded(self.palette.ink, amount),
                  width=core, joint="curve")

    @staticmethod
    def _faded(colour, amount: float):
        if len(colour) == 3:
            colour = colour + (255,)
        clamped = max(0.0, min(1.0, amount))
        return colour[:3] + (int(colour[3] * clamped),)


def rgba_to_premultiplied_bgra(image: Image.Image) -> bytearray:
    """Convert a Pillow RGBA image into what UpdateLayeredWindow expects.

    Two conversions at once: channel order (RGBA to BGRA) and premultiplied
    alpha. Pillow gives straight alpha; GDI wants each colour already scaled by
    its own alpha, or every soft edge grows a bright halo.

    Both halves run in Pillow's C code rather than a Python loop. The loop
    version cost 2.3ms a frame, which is most of a frame budget spent on
    arithmetic.
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    red, green, blue, alpha = image.split()
    # ImageChops.multiply computes (a * b) / 255 per pixel, which is exactly
    # the premultiply operation.
    premultiplied = Image.merge(
        "RGBA",
        (
            ImageChops.multiply(red, alpha),
            ImageChops.multiply(green, alpha),
            ImageChops.multiply(blue, alpha),
            alpha,
        ),
    )
    # The "BGRA" rawmode does the channel swap during packing, so there is no
    # second pass over the pixels.
    return bytearray(premultiplied.tobytes("raw", "BGRA"))

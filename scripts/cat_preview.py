"""Render every cat state to one PNG, so the sprite can be looked at.

This is the smallest instance of the loop in 00-scope.md: produce it, look at
it, repair it. A cat is judged by eye and by nothing else, so the sprite needs a
way to be seen without starting the whole overlay.

Each state is drawn on both a light and a dark strip, because the overlay sits
over whatever the user happens to have open.

    python scripts/cat_preview.py
    python scripts/cat_preview.py --size 192 --out cats.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from meow.cat import Animator, CatPalette, CatRenderer, CatState

# Not 0: at t=0 every sine is at zero and the cat sits perfectly still, which
# hides exactly the motion this preview exists to check.
SAMPLE_TIME = 1.7


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--out", default="cat_states.png")
    args = parser.parse_args()

    states = list(CatState)
    light_renderer = CatRenderer(width=args.width,
                                 palette=CatPalette.for_background(0.95))
    dark_renderer = CatRenderer(width=args.width,
                                palette=CatPalette.for_background(0.10))
    renderer = light_renderer
    padding = 14
    label_height = 18
    cell_width = renderer.width + padding * 2
    cell_height = renderer.height + padding * 2

    sheet = Image.new(
        "RGB",
        (cell_width * len(states), cell_height * 2 + label_height),
        (245, 245, 247),
    )
    draw = ImageDraw.Draw(sheet)
    draw.rectangle([0, cell_height, sheet.width, cell_height * 2],
                   fill=(28, 28, 32))

    for index, state in enumerate(states):
        animator = Animator(state=state, seed=index)
        if state is CatState.POINTING:
            animator.point_direction_x = 0.8
            animator.point_direction_y = -0.2
        if state is CatState.SPEAKING:
            animator.speech_level = 0.7

        pose = animator.pose_at(SAMPLE_TIME)
        x = index * cell_width + padding
        # Each strip gets the palette it would actually be drawn with, which is
        # the point of the two strips.
        light_frame = light_renderer.render(pose)
        dark_frame = dark_renderer.render(pose)
        sheet.paste(light_frame, (x, padding), light_frame)
        sheet.paste(dark_frame, (x, cell_height + padding), dark_frame)

        draw.text((x, cell_height * 2 + 4), state.value, fill=(40, 40, 46))

    output = Path(args.out)
    sheet.save(output)
    print(f"wrote {output.resolve()}  ({sheet.width}x{sheet.height})")


if __name__ == "__main__":
    main()

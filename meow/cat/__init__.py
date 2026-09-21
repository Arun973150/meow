"""The cat: how it is drawn, and how it moves."""

from .animation import Animator, CatState, blend_poses
from .sprite import CatPalette, CatPose, CatRenderer, rgba_to_premultiplied_bgra

__all__ = [
    "Animator",
    "CatState",
    "blend_poses",
    "CatPalette",
    "CatPose",
    "CatRenderer",
    "rgba_to_premultiplied_bgra",
]

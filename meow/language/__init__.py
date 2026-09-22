"""Spoken language: phrases, noise, agreement, and route corrections."""

from .phrases import (  # noqa: F401
    ADD_WORDS,
    ARTEFACT_WORDS,
    BACKGROUND_WORDS,
    CLOSE_WORDS,
    HANDS_ON_WORDS,
    MEANINGFUL_ALONE,
    MINIMUM_WORDS_FOR_A_PLAN,
    NO_WORDS,
    NOISE_WORDS,
    YES_NO_OPENERS,
    YES_WORDS,
    hears_yes,
    is_noise,
    spoken_words,
    starts_with_any,
    wants_its_own_window,
    without_trailing_yes_no,
)
from .routing import CONNECTOR_WORDS, correct  # noqa: F401

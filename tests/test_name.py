"""The cat has a name, and says hello once.

Small, but two things here are easy to get wrong in ways nobody notices until
it is annoying: a greeting that fires on every activation, and a name that is
written down in three places and changed in two.
"""

from __future__ import annotations

import pytest

from meow import config


def test_there_is_a_default_name():
    assert config.cat_name() == config.DEFAULT_CAT_NAME


def test_the_name_can_be_changed_in_one_place(monkeypatch):
    """CAT_NAME in .env. It is said in the greeting and it goes into both
    prompts, so hard-coding it would mean changing three files to rename a cat.
    """
    monkeypatch.setenv("CAT_NAME", "Biscuit")
    assert config.cat_name() == "Biscuit"


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_name_falls_back(monkeypatch, value):
    """A blank line in .env should not produce a cat called nothing."""
    monkeypatch.setenv("CAT_NAME", value)
    assert config.cat_name() == config.DEFAULT_CAT_NAME


def test_the_greeting_says_the_name_and_fits_the_bubble():
    """The speech bubble is hard-capped at 90 characters and three lines. A
    greeting that overflows it is silently truncated on screen.
    """
    from meow.app.loop import GREETING

    assert config.cat_name().lower() in GREETING
    assert len(GREETING) <= 90
    # Written for the ear: lowercase, no markdown, one sentence or two.
    assert GREETING == GREETING.lower()


def test_both_prompts_name_the_cat():
    """So "what's your name" has an answer on either path - the answer path
    and the harness are different models with different prompts.
    """
    from meow.agent.harness import SYSTEM_PROMPT as harness_prompt
    from meow.agent.mind import SYSTEM_PROMPT as answer_prompt

    for prompt in (harness_prompt, answer_prompt):
        assert config.cat_name() in prompt

"""Corrections applied to a route after the classifier has produced one.

Jev is a classifier and these are not classification problems - they are facts
about what the cat can do, which no amount of prompt tuning teaches a model
that cannot see the tool list. Each rule here exists because a real sentence
went somewhere useless, and each is structural for the same reason: the cost
of being wrong is asymmetric, so it is decided in code rather than weighed.

Pure functions over a Route and a transcript. No I/O, no model, no globals -
the whole point of lifting them out of a 742-line `main()`.
"""

from __future__ import annotations

from dataclasses import replace

from ..agent.router import Intent, Route
from .phrases import ARTEFACT_WORDS, MINIMUM_WORDS_FOR_A_PLAN, spoken_words

# Mail, calendar, weather and the rest live in the HARNESS as tools; the answer
# path has no tools at all. So "what is in my inbox" - which is shaped exactly
# like a question, and which Jev therefore calls ANSWER - reached a model that
# could only talk, and it talked about the screenshot it was handed: "i'm not
# looking at your screen right now, tell me what emails you see". Connected
# accounts were reachable by script and unreachable by voice, which is the only
# way anyone actually uses this.
CONNECTOR_WORDS = ("inbox", "email", "emails", "e-mail", "mail", "gmail",
                   "calendar", "agenda", "schedule", "meeting", "meetings",
                   "appointment", "appointments", "youtube", "video",
                   # Answerable only by asking a service. "What is the weather"
                   # from the model's own memory is a guess about today dressed
                   # as an answer.
                   "weather", "forecast", "hacker", "hackernews",
                   "todo", "to-do", "tasks")

# Deliberately NOT in CONNECTOR_WORDS: "document" and "spreadsheet". They are
# ARTEFACT_WORDS - they mean "make me one" far more often than "read my Google
# one" - and upgrading them to ACT would skip the artefact rule below, which is
# what makes producing a file about a topic a plan.


def needs_a_connected_account(transcript: str) -> bool:
    """Does answering this require asking a service rather than a model?"""
    return any(word in spoken_words(transcript).split()
               for word in CONNECTOR_WORDS)


def makes_a_file_about_something(transcript: str) -> bool:
    """Two jobs wearing one sentence: find out, then write it."""
    words = spoken_words(transcript)
    return (any(word in words for word in ARTEFACT_WORDS)
            and len(words.split()) >= MINIMUM_WORDS_FOR_A_PLAN)


def too_short_to_hand_over(transcript: str) -> bool:
    """Three words cannot describe a window, a thread and a plan."""
    return len(spoken_words(transcript).split()) < MINIMUM_WORDS_FOR_A_PLAN


def correct(route: Route, transcript: str) -> tuple[Route, str]:
    """The route Jev gave, put right. Returns it with a reason, or "".

    Order matters and is not arbitrary. The artefact rule runs first so that a
    request to build a file about a topic becomes a PLAN before anything else
    looks at it; the connector rule only ever promotes ANSWER, so it cannot
    undo that. SHOW is never touched by either - that is somebody asking how to
    do a thing themselves, and turning it into an action is the one mistake
    that would be actively rude.
    """
    if route.intent is Intent.ACT and makes_a_file_about_something(transcript):
        return (replace(route, intent=Intent.PLAN),
                "makes a file about something, so planning it")

    if route.intent is Intent.ANSWER and needs_a_connected_account(transcript):
        return (replace(route, intent=Intent.ACT),
                "that needs a connected account, so acting")

    return (route, "")


# NOT part of `correct`, and the reason is behavioural rather than tidy. In the
# loop this check runs AFTER the decision about whether a plan gets its own
# window, so it only ever applies to work somebody wanted handed over. Folding
# it in here would apply it first, and a short plan that wants no window would
# become an ACT instead of reaching the foreground planner - a different app,
# quietly. Kept separate so the loop keeps the order it has.

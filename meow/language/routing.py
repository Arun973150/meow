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
from .phrases import (
    ARTEFACT_WORDS,
    MINIMUM_WORDS_FOR_A_PLAN,
    spoken_words,
    wants_the_web,
)

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


# Questions that can only be answered by LOOKING at what is on screen. The
# answer path gets a low-detail screenshot and gpt-4o-mini, which was measured
# reading a chess board wrongly at every detail level - three tries, three
# invented knights. The harness has `look_at_screen`, which uses the seeing
# model and reads the same board correctly, and it has the recipe that says to
# look before advising. So these have to reach the harness.
#
# Live, before this: "what should be my next move" was answered "you could
# play e4", then "move your knight to f3" onto a square already holding their
# knight, then "knight a5 to c6, which checks the king" - with no knight on a5
# and no check. Confident, fluent, and invented.
LOOKING_WORDS = ("board", "position", "piece", "pieces", "chess", "puzzle",
                 "sudoku", "checkmate", "knight", "bishop", "rook", "pawn",
                 "queen", "king", "turn", "diagram", "graph", "chart")

LOOKING_PHRASES = ("best move", "next move", "my move", "should i play",
                   "whose turn", "what do you see", "on my screen",
                   "look at my screen", "see my screen", "what is this")


def needs_to_look(transcript: str) -> bool:
    """Can this only be answered by looking at the screen?"""
    words = spoken_words(transcript)
    if any(phrase in words for phrase in LOOKING_PHRASES):
        return True
    return any(word in words.split() for word in LOOKING_WORDS)


def needs_a_connected_account(transcript: str) -> bool:
    """Does answering this require asking a service rather than a model?"""
    return any(word in spoken_words(transcript).split()
               for word in CONNECTOR_WORDS)


# What a genuine SHOW turn asks. The intent is to be TAUGHT - the user wants
# to do it themselves next time - and every one of these frames a question
# about method rather than about content.
ASKS_HOW = ("how do", "how can", "how to", "how would", "how does",
            "where is", "where are", "where do", "where can", "where would",
            "show me how", "teach me", "walk me through", "take me to",
            "guide me", "what is the way", "whats the way")


def asks_how_rather_than_what(transcript: str) -> bool:
    """Is this a request for instructions, or for the thing itself?

    "How do I check my mail" wants to be shown. "What's in my inbox" wants the
    mail. Jev calls both SHOW often enough to matter, and the difference is
    not a shade of meaning - one hands back a route to read out and the other
    has to reach an account.
    """
    return any(phrase in spoken_words(transcript) for phrase in ASKS_HOW)


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

    if (route.intent in (Intent.ANSWER, Intent.SHOW)
            and needs_a_connected_account(transcript)
            and not asks_how_rather_than_what(transcript)):
        # SHOW is included here and nowhere else in this module, narrowly.
        # Jev called "what's in my inbox" SHOW on one run and ANSWER on
        # another - it is genuinely ambiguous read as a sentence - and SHOW
        # refuses every tool that could reach an account, so that run answered
        # with a route nobody asked for. Asking HOW still stays SHOW, which is
        # the case the refusal exists to protect.
        return (replace(route, intent=Intent.ACT),
                "that needs a connected account, so acting")

    if route.intent is Intent.ANSWER and needs_to_look(transcript):
        # The answer path holds no tools, so it answers about a chess board
        # from a low-detail screenshot and a model that cannot read one. The
        # harness can look properly and can mark what it finds.
        return (replace(route, intent=Intent.ACT),
                "that needs looking at the screen, so acting")

    if route.intent is Intent.ANSWER and wants_the_web(transcript):
        # "Do a research on gpu prices in india" and "look up the best laptops
        # under fifty thousand" both scored ANSWER - which is what they look
        # like, and is the one thing they must not be. The answer path holds
        # no tools, so the cat replies out of training data: confident, fluent
        # and a year out of date, with nothing in the reply to suggest it
        # never looked. Being asked to find something out is, structurally, a
        # statement that the model's own knowledge is not the answer.
        return (replace(route, intent=Intent.ACT),
                "that needs looking up, so acting")

    return (route, "")


# NOT part of `correct`, and the reason is behavioural rather than tidy. In the
# loop this check runs AFTER the decision about whether a plan gets its own
# window, so it only ever applies to work somebody wanted handed over. Folding
# it in here would apply it first, and a short plan that wants no window would
# become an ACT instead of reaching the foreground planner - a different app,
# quietly. Kept separate so the loop keeps the order it has.

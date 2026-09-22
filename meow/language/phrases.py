"""Understanding what somebody said - the words, not the audio.

Separate from `meow.voice`, which is microphones and sockets. This is the
layer that decides whether a transcript is an instruction, a noise, a yes, or
a sentence belonging to a task already running. It holds no I/O, reaches no
network and imports nothing from the app, so it can be tested with plain
strings - which is the point. It lived in `scripts/meow.py` until the tests
had to load a *script* through importlib to reach it.

Every list here was widened by a real failure, and the comments say which.
"""

from __future__ import annotations

import re

YES_WORDS = ("yes", "yeah", "yep", "sure", "go ahead", "do it", "okay", "ok",
             "please do", "confirm", "alright")
NO_WORDS = ("no", "nope", "don't", "do not", "stop", "cancel", "leave it",
            "never mind", "nevermind", "wait")

# Saying one of these while a task is running adds to it instead of
# starting something new. Explicit rather than inferred: guessing whether
# a sentence belongs to a running task gets it wrong in both directions,
# and being wrong means either a lost instruction or a hijacked one.
ADD_WORDS = ("also", "and also", "add", "as well", "on top of that",
             "tell it to", "make it", "include")
CLOSE_WORDS = ("close that", "close them", "close it", "dismiss",
               "get rid of", "clear that", "clear them", "close the task")

# Nothing here carries a request on its own. "yeah" and "okay" are in the
# list because alone they are acknowledgement - the confirmation check
# runs before this one, so a real yes still gets through.
NOISE_WORDS = {"oh", "uh", "um", "hmm", "hm", "mm", "mhm", "ah", "eh",
               "huh", "yeah", "yep", "okay", "ok", "right", "so", "well",
               "hey", "you", "thanks", "thank", "bye", "a", "the", "i",
               "and", "like", "just", "its", "it", "is", "that"}

# Single words that really are requests. Everything else said alone is
# treated as noise, whatever language it is in - a word-list cannot cover
# every language the transcriber might produce, but "one word is not a task"
# holds in all of them. A Hindi "haan" was routed to plan and handed its own
# background window, which no English filler list would ever have caught.
MEANINGFUL_ALONE = {"stop", "cancel", "close", "dismiss", "pause", "undo",
                    "help", "wait", "quiet", "mute", "back", "enter", "escape"}

# A background task is a window, a thread and a plan. Three words cannot
# describe one, so anything shorter is a misroute rather than a small job -
# and a misroute that opens a window is worse than one that does not.
MINIMUM_WORDS_FOR_A_PLAN = 4

# Producing a file ABOUT something is always at least two jobs: find out, then
# write it. Routed as one action it runs in the foreground and blocks the voice
# loop for half a minute with no icon and no way to watch it - which is what
# "About GPU prices in India. Put it in a spreadsheet." did, purely because the
# word "research" was never said.
ARTEFACT_WORDS = ("spreadsheet", "document", "deck", "slides", "presentation",
                  "report", "essay", "xlsx", "docx", "pptx")

# Work that earns its own window: it runs for a while and the user is meant to
# walk away from it. Everything else multi-step - open this, click that, type
# there - is a sequence the user is watching happen, and putting a window in
# front of them to narrate what they can already see is clutter. Those run in
# the foreground with the thinking animation.
BACKGROUND_WORDS = ("research", "find out", "look up", "read about",
                    "compare", "summarise", "summarize", "gather", "collect",
                    "report on", "write up", "search the web", "search online")

# Verbs that mean the user is pointing at their own screen. Bare "search" is
# not in BACKGROUND_WORDS because it belongs to both worlds - searching the
# web is work to walk away from, searching in Chrome is four clicks someone
# is watching - so a hands-on verb anywhere in the sentence decides it.
HANDS_ON_WORDS = ("click", "press", "type", "minimise", "minimize", "maximise",
                  "maximize", "scroll", "select", "tab", "paste", "copy")

# Words that open a yes/no question. A reply ending in one invites a bare
# "yes", and a bare yes is the most dangerous thing the user can say - it
# carries no instruction, so whatever the agent had half-planned gets done.
# Live, "i wrote a brief piece about elon musk. would you like to see it?"
# collected a "Yes" and typed the whole paragraph a second time.
YES_NO_OPENERS = {"would", "do", "does", "did", "can", "could", "shall",
                  "should", "will", "is", "are", "was", "were", "have",
                  "has", "may", "must", "want", "am"}


def hears_yes(text: str) -> bool | None:
    """Did they agree? None when it was neither.

    Checked in this order because "no" is a substring of "nope" but also of
    "not now" - and a false yes presses something nobody asked for, while a
    false no just asks again.
    """
    lowered = f" {text.lower().strip()} "
    if any(f" {word} " in lowered or lowered.strip().startswith(word)
           for word in NO_WORDS):
        return False
    if any(f" {word} " in lowered or lowered.strip().startswith(word)
           for word in YES_WORDS):
        return True
    return None


def spoken_words(text: str) -> str:
    """Lowercased, unpunctuated, single-spaced.

    Transcripts arrive punctuated - "Yeah, close it." - and every phrase
    here is written without punctuation. "close it" is not inside
    "close it." because of the full stop, which is why saying "close it"
    sent the sentence to the agent, which closed Notepad, instead of
    dismissing the finished task.
    """
    letters = "".join(character if character.isalnum() or character.isspace()
                      else " " for character in text.lower())
    return " ".join(letters.split())


def starts_with_any(text: str, phrases) -> bool:
    lowered = spoken_words(text)
    return any(lowered.startswith(phrase) or f" {phrase} " in f" {lowered} "
               for phrase in phrases)


def wants_its_own_window(text: str) -> bool:
    """Is this long work, or steps the user is watching?

    Matched on what the job NEEDS rather than how long the sentence is. A
    short sentence can start half an hour of research, and a long one can be
    four clicks.
    """
    lowered = spoken_words(text)
    if any(word in lowered.split() for word in HANDS_ON_WORDS):
        return False
    # Artefacts count as background work too, and the two lists have to agree:
    # "deck" was in one and not the other, so "make a deck about the history of
    # computing" planned correctly and then ran in the foreground anyway.
    return (any(phrase in lowered for phrase in BACKGROUND_WORDS)
            or any(word in lowered for word in ARTEFACT_WORDS))


def without_trailing_yes_no(text: str) -> str:
    """Drop a closing yes/no question, keeping what came before it.

    AGENTS.md forbids these and the prompt says so twice; the model does it
    anyway, so it is enforced here rather than asked for. Only a trailing one
    is removed, and only when something else was said - a reply that is
    nothing but a question is a real request for information, and swallowing
    it would leave silence, which is worse.
    """
    stripped = text.rstrip()
    if not stripped.endswith("?"):
        return text
    sentences = re.split(r"(?<=[.!?])\s+", stripped)
    if len(sentences) < 2:
        return text
    opening = spoken_words(sentences[-1]).split()
    if opening and opening[0] in YES_NO_OPENERS:
        return " ".join(sentences[:-1])
    return text


def is_noise(text: str) -> bool:
    """A breath, not a request.

    The transcriber emits these on breaths, background talk and the tail
    of a sentence it already sent. Passing one to the router is not
    harmless: "Oh." was routed to plan, handed to a background task, and
    got its own window before failing with "could not break that into
    steps". Answering them out loud is its own problem - a companion that
    replies to every noise teaches people to stop talking near it.

    Only short utterances qualify. Three words of nothing is a noise;
    four words is someone talking, even if it opens with "oh".
    """
    words = spoken_words(text).split()
    if not words:
        return True
    if len(words) == 1:
        # Language-independent. The transcriber emits single words constantly
        # from breaths and background talk, and one word is not an instruction
        # in any language.
        return words[0] not in MEANINGFUL_ALONE
    return len(words) <= 3 and all(word in NOISE_WORDS for word in words)

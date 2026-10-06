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


# Sentences that want the WEB, not the window in front. A research question
# asked while Chrome happens to be focused was answered by reaching into
# Chrome - clicking the address bar, typing a query - because the turn injects
# a digest of the foreground window and a browser's digest is full of
# plausible things to press. The user asked what GPU prices ARE; driving their
# browser is a different act that happens to involve the same words.
#
# The digest is withheld for these, rather than the model being asked nicely
# to ignore it. A list of clickable controls sitting next to a question is not
# something a prompt reliably outranks - the same reasoning as `guiding`
# refusing in the tools rather than in the prompt.
WEB_PHRASES = ("research", "look up", "look it up", "find out", "google",
               "search for", "search up", "find information", "find me info",
               "what are the latest", "read up on", "gather information")

# ...unless they named the desktop themselves. "Search for it in chrome" and
# "click the address bar and search" are requests to drive the browser, and
# the screen is exactly what those need.
DESKTOP_WORDS = ("chrome", "browser", "edge", "firefox", "address bar", "tab",
                 "click", "press", "type", "open", "window", "notepad",
                 "word", "excel", "on screen", "on my screen", "this page")


def wants_the_web(transcript: str) -> bool:
    """True when the question is about the world, not about this window."""
    lowered = f" {str(transcript).lower().strip()} "
    if not any(phrase in lowered for phrase in WEB_PHRASES):
        return False
    return not any(word in lowered for word in DESKTOP_WORDS)


# --- following a walkthrough -------------------------------------------------
#
# A walkthrough used to end on ANY sentence, because the loop cancelled it
# before routing: "ok what next" destroyed the thing that knew what next was,
# and so did "i can't find it", and so did a cough the transcriber heard as a
# word. The goal and every step already walked went with it.
#
# Matched BEFORE routing, locally, never reaching a model. Not a cost
# decision - "next" is not a classification problem, and a sentence that needs
# a round trip to be understood is not a reflex.

# `spoken_words` turns an apostrophe into a SPACE, so "can't" arrives as
# "can t" and "I'm" as "i m". That is deliberate and tested elsewhere, and
# it makes a phrase list unreadable. These comparisons glue the fragment back
# on first, which changes nothing for any other caller.
_CONTRACTION_TAILS = ("s", "t", "m", "re", "ve", "ll", "d")


def without_split_contractions(text: str) -> str:
    """`spoken_words`, with "can t" read back as "cant".

    Only for comparing against the phrase lists below. Rejoining is safe
    because none of these tails is an English word on its own - the one risk
    would be "d" as a letter, and nobody says a bare letter mid-sentence to a
    walkthrough.
    """
    words = spoken_words(text).split()
    joined: list[str] = []
    for word in words:
        if joined and word in _CONTRACTION_TAILS:
            joined[-1] += word
        else:
            joined.append(word)
    return " ".join(joined)


# "Carry on" - they did the step, or they want the next one regardless.
NEXT_STEP_PHRASES = frozenset({
    "next", "next step", "next one", "whats next", "what next", "then what",
    "and then", "continue", "carry on", "go on", "keep going", "done",
    "i did it", "did it", "thats done", "ok done", "okay done", "okay next",
    "ok next", "finished", "im there", "i am there", "i got it", "got it",
    "yep done", "yeah done", "now what",
})

# "Say that again" - the step is right and they lost the words. Different from
# being lost: they know where they are.
REPEAT_PHRASES = frozenset({
    "say that again", "again", "repeat", "repeat that", "what was that",
    "sorry what", "come again", "one more time", "say it again",
})

# "I cannot find it" - the step is right and the screen is not helping. This
# is the one that wants POINTING rather than talking.
STUCK_PHRASES = frozenset({
    "i cant find it", "cant find it", "i cannot find it", "cannot find it",
    "i dont see it", "dont see it", "i do not see it", "where is it",
    "wheres it", "where", "im lost", "i am lost", "not there",
    "its not there", "it is not there", "i dont know where", "i cant see it",
    "cant see it",
})

# Leaving. A walkthrough is help, not a mode - but ending it should be
# something they can SAY, not only something that happens by accident.
LEAVE_PHRASES = frozenset({
    "stop", "stop it", "never mind", "nevermind", "forget it", "leave it",
    "cancel", "quit", "im done", "i am done", "thats enough",
    "that is enough", "no thanks", "stop the walkthrough", "stop teaching",
})


# People acknowledge before they ask. "ok what next", "alright so what next"
# and "yeah done" are all the same instruction wearing a different number of
# throat-clearings, and an exact match against "what next" catches none of
# them. Stripped from the FRONT only, and only for the four comparisons below
# - this is not a general noise filter, which `is_noise` already is.
#
# "no" is deliberately absent. It is an instruction on its own everywhere else
# in this file, and a leading-filter that ate it would turn "no, stop" into
# something else entirely.
# "and", "then" and "now" are deliberately absent too, for the same reason
# in the other direction: "then what", "and then" and "now what" ARE the
# instruction, and a filter that ate the first word left "what" behind.
_LEADING_FILLER = frozenset({
    "ok", "okay", "kay", "alright", "right", "so", "well", "um", "uh", "oh",
    "ah", "hmm", "hey", "yeah", "yep", "yes", "sure", "cool", "great",
    "good", "please", "just", "momo",
})


def _for_matching(text: str) -> str:
    """Spoken words, contractions rejoined, acknowledgements stripped."""
    words = without_split_contractions(text).split()
    while words and words[0] in _LEADING_FILLER:
        words.pop(0)
    return " ".join(words)


def wants_the_next_step(text: str) -> bool:
    """Are they telling us to carry on with the route they are following?"""
    return _for_matching(text) in NEXT_STEP_PHRASES


# The same question, asked the way people actually ask it. "Okay, what next
# to do?" is "what next" with two words in the way, and an exact match missed
# it - so the lesson did not advance, the sentence routed as a question, and
# the cat said "what's on your screen right now? describe it to me" to
# somebody who was waiting to be told the next step.
#
# Loose ON PURPOSE, and only ever consulted while a lesson is LIVE. Outside
# one these words mean other things; inside one there is a step on the table
# and this is the only thing the sentence could be about. See
# `Guide.answer`, which is the only caller.
# Multi-word and distinctive ONLY. A bare "ok", "right" or "yeah" is handled
# by the whole-sentence rule below instead, because as a CONTAINMENT test they
# match anything: "Yourself? You can check my screen, right?" was swallowed as
# "go on" on the strength of its last word, and the question went unanswered.
FOLLOWING_ALONG = (
    # Asking for the next one.
    "what next", "whats next", "next step", "next one", "then what",
    "and then", "what now", "now what", "what do i do", "what should i do",
    "carry on", "go on", "keep going", "move on",
    # Reporting that the current one is behind them. These lived in
    # `guiding.py` as a second list doing the same job in a different file -
    # which is how one of them got `spoken_words` instead of
    # `without_split_contractions` and missed every contraction it was
    # written for. One list, one normaliser, one place to add to.
    "did it", "done it", "i did", "i have done", "ive done", "i added",
    "ive added", "i have added", "added it", "i pressed", "ive pressed",
    "i clicked", "ive clicked", "i selected", "ive selected", "i made",
    "ive made", "i typed", "ive typed", "i opened", "ive opened",
    "that is done", "thats done", "its done", "it is done", "finished it",
    "i am there", "im there", "im done",
)

# A sentence that is NOTHING BUT one of these, mid-lesson, is "go on". They
# are separated from the hesitations below because the two are opposite
# signals wearing the same shape: "yeah" means carry on, "um" means somebody
# is still thinking - and treating a cough as consent advanced a step the
# user had not taken.
ACKNOWLEDGEMENTS = frozenset({
    "yeah", "yep", "yes", "ok", "okay", "kay", "right", "sure", "done",
    "finished", "continue", "next", "now", "cool", "great", "good", "alright",
    "got it", "all done", "yeah done", "ok done",
})


def moving_on(text: str) -> bool:
    """Mid-lesson, is this "go on" in some shape?

    Covers both halves of the same signal - asking for the next step, and
    reporting that this one is done - because mid-lesson they mean the same
    thing and splitting them across two files is how one of them ended up
    with the wrong normaliser.

    Containment rather than equality, which is safe ONLY because the caller
    has established that somebody is halfway through being taught. Outside a
    lesson these words mean other things entirely.
    """
    whole = " ".join(without_split_contractions(text).split())
    if whole in ACKNOWLEDGEMENTS:
        # The whole sentence is an acknowledgement: a bare "yeah", "ok",
        # "done". Outside a lesson that is noise and is dropped; inside one,
        # waiting for the next step, it is the entire answer - and it was
        # being thrown away before the walkthrough ever saw it.
        return True
    said = _for_matching(text)
    if not said:
        # Everything stripped away and it was NOT an acknowledgement, so it
        # was hesitation - "um", "uh", "hmm". Somebody still thinking is not
        # somebody who has finished, and advancing on one is worse than
        # waiting.
        return False
    if said in ACKNOWLEDGEMENTS:
        # "Right, continue" - an acknowledgement in front of another one.
        # What is left after the leading filler goes is the whole
        # instruction, so it is judged the same way the whole sentence was.
        return True
    if any(phrase in said for phrase in FOLLOWING_ALONG):
        return True

    # A SHORT sentence ENDING in an acknowledgement. Structural rather than a
    # word list, which is the only kind of rule that survives a language
    # nobody planned for: "accha done" and "haan done" are two words ending
    # in "done" and cannot be anything else mid-lesson, while "i am done
    # with this stupid thing" is six and is not an instruction to carry on.
    #
    # The leading word is never examined. It does not need to be understood,
    # only not to get in the way.
    words = whole.split()
    return 1 < len(words) <= 3 and words[-1] in ACKNOWLEDGEMENTS


def asks_to_repeat(text: str) -> bool:
    """Do they want the current step said again?"""
    return _for_matching(text) in REPEAT_PHRASES


def cannot_find_it(text: str) -> bool:
    """Are they looking for the current step and not seeing it?"""
    return _for_matching(text) in STUCK_PHRASES


def wants_to_stop_following(text: str) -> bool:
    """Are they leaving the walkthrough, rather than asking for something else?

    Matched EXACTLY, like the rest of this group. "stop" on its own ends a
    walkthrough; "stop the music" is a request, and the difference between
    them is the whole sentence rather than the first word.
    """
    return _for_matching(text) in LEAVE_PHRASES

"""What the microphone actually produced, and what has to happen to it.

**Every transcript in this file was spoken out loud at this machine and
printed by the running app.** None of them is invented and none is tidied up -
"Can you open notepad for B?" is what came back when somebody said "for me",
and that is the input the code has to survive.

This file exists because the automated suites kept passing while live runs
kept failing. The suites were written against sentences a developer types -
clean, complete, punctuated the way the phrase lists are - and the microphone
produces something else. Four kinds of damage, all of them here:

    dropped words       "An you open a new tab on it?"     (can you)
    homophones          "direct me towards the river"      (liver)
    run-together        "Xiaomi where is hello can you"    (show me)
    swallowed starts    "S in my inbox."                   (what's)

Everything checked here is DETERMINISTIC - the noise filter, the lesson
dispatch, the route corrections. The model's own classification is measured
live and separately by `meow routing`, which replays the same corpus.
"""

from __future__ import annotations

import pytest

from meow.agent.router import Intent, Route
from meow.app import guiding
from meow.language import is_noise
from meow.language.phrases import moving_on
from meow.language.routing import correct

# --- the corpus, grouped by what the person was trying to do ----------------

OPENING_SOMETHING = [
    "Hey, can you open Blender for me?",
    "Open Blender for me?",
    "Open blender.",
    "Can you open notepad for B?",            # "for me"
    "An you open a new tab on it?",           # "Can you"
    "Minimizes the notepad.",                 # "minimise the notepad"
    "Minimize VS code for me and open blender.",
    "Hey, can you minimize the vs code and open blender only?",
]

ASKING_TO_BE_TAUGHT = [
    "Can you teach me how to create a new file here?",
    "Hey, can you teach me how to open new files here?",
    "Okay, now can you teach me how to make an animation of a ball "
    "bouncing in a simple plane?",
    "Hey, can you teach me how to make a ball bounce on a flat like "
    "area animation?",
    "Okay, now I want to make a ball bounce over a plane animation. "
    "Tell me how to do it.",
    "How do I do a simple animation of a ball jumping on a single plane?",
    "I don't know how to change the dark mode to light mode.",
    "Can you tell me how to delete everything here?",
]

ASKING_WHERE_SOMETHING_IS = [
    "Where do I get personalization?",
    "Where is the minimize?",
    "Where is the keyframe section? Can you show me it?",
    "Can you tell me where do I set the interpolation?",
    "Can you show me where is.",               # trailed off
    "Xiaomi where is hello can you show.",     # "show me where is hello"
    "Can you direct me towards the river?",    # "liver", on an anatomy page
    "Okay, can you show me how to do it?",
]

FOLLOWING_A_LESSON = [
    "Yeah. What's next?",
    "Yes. What's next?",
    "What's next?",
    "Yeah. What next?",
    "Okay, what next?",
    "Okay, what next to do?",
    "Okay, what next to do?",
    "Done.",
    "Yeah.",
    "Now.",
    "Okay.",
]

REPORTING_A_STEP_DONE = [
    "Yeah, I've added the ball now.",
    "Okay, I pressed I and selected the initial position. What next?",
    "I clicked it.",
    "I've selected the ball.",
    "I did it.",
]

BEING_HUMAN = [
    "That's a wrong command.",
    "It's not working. Can you show me how to open a new file based on "
    "notifying screen?",
    "You were teaching me how to animate it. Did you forget it?",
    "Yourself? You can check my screen, right?",
    "Can you see it? What's on my screen?",
    "Can I show you the result? Can you point me out?",
    "Yeah. So now how do I make this ball bounce? So I've set the initial "
    "keyframe here.",
]

# Said at the microphone and meaning nothing. Each of these was routed
# somewhere once: "Oh." was given its own background task and window, and a
# Hindi "haan" was too - which is why the rule that survives is structural
# rather than a word list.
JUST_NOISE = ["Oh.", "S?", "um", "hmm", "uh huh", "haan", "हाँ", "mm", ""]


def lesson(*steps):
    """A Guide with a live procedure lesson and no threads or screen.

    `_point_now` is replaced because it reads the real accessibility tree,
    and a test that touches UIA from a worker thread fills the output with
    "CoInitialize has not been called" and tells you nothing about the
    sentence it was meant to be checking.
    """
    watcher = guiding.Guide(say=lambda _s: None)
    watcher.said_lines = []
    watcher.say = watcher.said_lines.append
    watcher.pointed_at = []
    watcher._point_now = lambda: watcher.pointed_at.append(
        watcher.walkthrough.current if watcher.walkthrough else None)
    watcher._start_watching = lambda _w: None
    watcher.teach(list(steps) or ["press shift a and choose mesh",
                                  "press i and choose location",
                                  "move to frame twenty",
                                  "press i again"], "animate a bouncing ball")
    return watcher


# --- noise, which is not noise while somebody is being taught ---------------


@pytest.mark.parametrize("said", JUST_NOISE)
def test_noise_is_dropped_when_nothing_is_going_on(said):
    assert is_noise(said)


@pytest.mark.parametrize("said", ["Done.", "Yeah.", "Now.", "Okay."])
def test_the_same_noise_is_the_SIGNAL_inside_a_lesson(said):
    """These were printed as "(ignored:)" in a live run while the user sat
    waiting to be told the next step. "Done" is the entire thing a procedure
    lesson advances on, and it is one word and all filler.
    """
    assert is_noise(said), "still noise on its own - the filter is right"
    watcher = lesson()
    before = watcher.walkthrough.index
    assert watcher.answer(said) is True, said
    assert watcher.walkthrough.index == before + 1


def test_a_cough_inside_a_lesson_is_still_dropped():
    """Suspending the filter must not mean every stray sound advances a
    step. "um" is not somebody saying they have finished.
    """
    watcher = lesson()
    assert watcher.answer("um") is False
    assert watcher.walkthrough.index == 0


# --- following along, in every shape it arrived in --------------------------


@pytest.mark.parametrize("said", FOLLOWING_A_LESSON)
def test_every_real_way_of_asking_for_the_next_step(said):
    """"Okay, what next to do?" is "what next" with two words in the way.
    The exact match missed it, so the lesson did not advance and the sentence
    routed as a question - answered with "what's on your screen right now?
    describe it to me", to somebody waiting on a step.
    """
    assert moving_on(said), said


@pytest.mark.parametrize("said", REPORTING_A_STEP_DONE)
def test_reporting_what_you_did_advances_the_lesson(said):
    watcher = lesson()
    assert watcher.answer(said) is True, said
    assert watcher.walkthrough.index == 1


@pytest.mark.parametrize("said", OPENING_SOMETHING + ASKING_TO_BE_TAUGHT)
def test_a_real_request_never_counts_as_following_along(said):
    """False is what sends a sentence to be routed normally. Every one of
    these contains a word from one of the lists.
    """
    assert not moving_on(said), said


@pytest.mark.parametrize("said", ASKING_WHERE_SOMETHING_IS + BEING_HUMAN)
def test_a_question_mid_lesson_is_not_swallowed_and_not_fatal(said):
    """Two failures at once, both seen live. The lesson must not advance on
    a question - the user has not done the step - and it must not END either,
    which it did: "Where is the keyframe section?" cancelled the walkthrough,
    and the next "what's next" had nothing to advance.
    """
    watcher = lesson()
    assert watcher.answer(said) is False, f"{said!r} was swallowed"
    # answer() returning False does not cancel: the loop decides, on the
    # route. What must be true here is that the lesson is still standing.
    assert watcher.walkthrough is not None
    assert watcher.walkthrough.index == 0


def test_leaving_is_still_possible_in_the_middle_of_a_lesson():
    watcher = lesson()
    assert watcher.answer("never mind") is True
    assert watcher.walkthrough is None


# --- the route corrections, on real transcripts -----------------------------


def test_a_swallowed_start_still_reaches_the_mailbox():
    """"What's in my inbox" arrived as "S in my inbox." - the first two words
    gone. The connector promotion keys on "inbox", which survived, and that
    is why it is a word rule rather than a sentence rule.
    """
    route, why = correct(Route(Intent.SHOW, True, False, "model"),
                         "S in my inbox.")
    assert route.intent is Intent.ACT
    assert why


def test_a_question_about_the_screen_reaches_a_tool():
    """The answer path holds no tools. "What's on my screen" must not be
    answered there, and `needs_to_look` is what moves it.

    Scoped tightly on purpose. "Can you direct me towards the river?" is NOT
    in here: it is a request to point at something, which SHOW already does
    properly, and widening `needs_to_look` to catch every noun on a page
    would send half the session through a vision call.
    """
    said = "Can you see it? What's on my screen?"
    route, why = correct(Route(Intent.ANSWER, True, False, "model"), said)
    assert route.intent is Intent.ACT
    assert why


def test_switching_windows_is_not_a_lesson():
    """SHOW refuses open_app and switch_app, so the cat points at the taskbar
    and the window stays where it was.
    """
    route, _why = correct(Route(Intent.SHOW, False, False, "model"),
                          "switch to my blender window")
    assert route.intent is Intent.ACT


@pytest.mark.parametrize("said", ASKING_TO_BE_TAUGHT)
def test_asking_to_be_taught_is_never_turned_into_doing(said):
    """The one correction that would be actively rude. Somebody who said
    "teach me" wants to do it themselves.
    """
    route, why = correct(Route(Intent.SHOW, False, False, "model"), said)
    assert route.intent is Intent.SHOW, said
    assert why == ""


# --- the damage the transcriber does, as rules ------------------------------


@pytest.mark.parametrize("said", [
    "Yeah. What's next?",
    "Yes. What's next?",
    "Yeah, I've added the ball now.",
    "I've pressed it.",
    "That's done.",
    "It's done.",
])
def test_contractions_survive_the_apostrophe_split(said):
    """`spoken_words` turns an apostrophe into a SPACE, so "what's" arrives
    as "what s" and "I've" as "i ve". Every phrase list is written without
    apostrophes, so every one of them misses unless the fragment is glued
    back on - which is exactly the bug the second copy of this list had, in
    a different file, with the wrong normaliser.
    """
    assert moving_on(said), said


def test_a_trailing_question_mark_on_a_statement_changes_nothing():
    """The transcriber punctuates by prosody, so a flat statement comes back
    as a question: "Open Blender for me?" was an instruction.
    """
    watcher = lesson()
    assert watcher.answer("Open Blender for me?") is False


@pytest.mark.parametrize("said", [
    "Minimizes the notepad.",          # "minimise the notepad"
    "An you open a new tab on it?",    # "Can you"
    "Can you open notepad for B?",     # "for me"
])
def test_a_mangled_instruction_is_still_an_instruction(said):
    """None of these is noise, none is following along, and all three must
    reach the router rather than being handled locally.
    """
    assert not is_noise(said), said
    assert not moving_on(said), said


def test_a_homophone_is_not_something_this_layer_can_fix():
    """"the river" for "the liver" is a word the transcriber got wrong, and
    nothing here can know that. What matters is that it is treated as a
    request to find something rather than silently dropped - the failure
    mode to avoid is confident silence, not a wrong noun.
    """
    said = "Can you direct me towards the river?"
    assert not is_noise(said)
    route, _why = correct(Route(Intent.SHOW, True, False, "model"), said)
    assert route.intent is Intent.SHOW


# --- the routing race, which no model test could catch ----------------------
#
# `meow routing` scores 56/56 on these exact transcripts. "Open blender."
# still went to SHOW live, and the cat answered "to open blender, say 'do
# it,' and i'll start the application" - because the route it used had been
# computed for the PREVIOUS sentence.


@pytest.mark.parametrize("partial, final", [
    ("can you show me where is", "Open blender."),
    ("can you show me where is", "Catch me up?"),
    ("i dont know how to change dark mode", "What is this?"),
    ("where is the keyframe section can you show me it", "Done."),
])
def test_a_previous_sentence_is_never_mistaken_for_this_one(partial, final):
    """The old rule had a FLOOR of three words, so for a two-word final the
    threshold was max(3, 1) = 3 - which accepted ANY partial of three words
    or more, including the one left over from the last thing said.
    """
    from meow.agent.router import _close_enough

    assert not _close_enough(partial, final)


@pytest.mark.parametrize("partial, final", [
    ("open blen", "Open blender."),
    ("open blender", "Open blender."),
    ("minimize vs code for me and open", "Minimize VS code for me and open blender."),
    ("hey can you teach me how to open new files", "Hey, can you teach me how to open new files here?"),
])
def test_the_real_partial_for_this_sentence_is_accepted(partial, final):
    """The whole point of routing interims is that the answer is ready when
    the sentence ends. The old rule REJECTED "open blen" for "Open blender."
    while accepting a different sentence entirely - exactly backwards.
    """
    from meow.agent.router import _close_enough

    assert _close_enough(partial, final)


def test_forgetting_a_sentence_also_cancels_its_routes_in_flight():
    """Clearing `latest` was not enough: a worker still passed its generation
    check and wrote itself back AFTER the turn had resolved, which is how a
    stale route was sitting there in time for the next sentence.
    """
    from meow.agent.router import Router

    router = Router(use_model=False)
    before = router._generation
    router._forget()
    assert router._generation > before
    assert router.latest is None


# --- the messier end of what a microphone does ------------------------------
#
# Everything above is a transcript this machine produced. These are the
# damage patterns BEHIND those transcripts, pushed harder - because the next
# live run will produce a new set and the rules have to survive them too,
# not just the ones already seen.


STUTTERS_AND_RESTARTS = [
    "can you can you open blender",
    "open open notepad",
    "i want to i want to make a ball bounce",
    "where where is the keyframe",
    "um can you teach me how to add a modifier",
    "so uh how do i set the interpolation",
]

RUN_ONS = [
    "okay so now i want you to teach me how to make a ball bounce on a plane "
    "and also show me where the keyframe button is because i cannot find it",
    "hey so i was wondering if you could maybe show me how to open a new file "
    "in blender because ctrl n did not seem to do anything at all",
]

TRAILED_OFF = [
    "can you show me where is",
    "how do i",
    "where is the",
    "okay so the thing is",
]

MIXED_IN = [
    "haan okay what next",
    "theek hai what next",
    "accha done",
]


@pytest.mark.parametrize("said", STUTTERS_AND_RESTARTS + RUN_ONS)
def test_a_stutter_or_a_run_on_is_still_a_request(said):
    """None of these is noise and none is "go on". They have to reach the
    router, where a model can read them - which is the one thing a model is
    better at than a word list.
    """
    assert not is_noise(said), said
    assert not moving_on(said), said


@pytest.mark.parametrize("said", TRAILED_OFF)
def test_a_trailed_off_sentence_is_not_mistaken_for_following_along(said):
    """"Can you show me where is." was real, and it is a half-finished
    question. Advancing a lesson on it would skip a step the user never did.
    """
    watcher = lesson()
    assert watcher.answer(said) is False, said
    assert watcher.walkthrough.index == 0


@pytest.mark.parametrize("said", MIXED_IN)
def test_an_acknowledgement_in_another_language_does_not_block_the_english(said):
    """The rule that survives translation is structural, and this is the
    limit of it: a Hindi acknowledgement in front of an English instruction
    must not stop the English being read. It does not have to UNDERSTAND
    "haan" - only not be derailed by it.
    """
    assert moving_on(said), said


def test_a_bare_word_in_another_language_is_still_noise():
    """"haan" alone was once routed to plan and given its own background
    window. One word is not an instruction, in any language - and that stays
    true inside a lesson, where it is not an English acknowledgement either.
    """
    assert is_noise("haan")
    watcher = lesson()
    assert watcher.answer("haan") is False


@pytest.mark.parametrize("said", [
    "SHIFT A",
    "Shift+A",
    "ctrl n",
    "press I",
    "frame 20",
    "the 3D viewport",
])
def test_keys_and_numbers_are_not_noise(said):
    """A lesson is full of these and they are short. "press I" is two words
    and one of them is a letter; dropping it as filler would eat half of
    what somebody says while being taught Blender.
    """
    assert not is_noise(said), said


def test_a_very_long_sentence_does_not_fall_through_every_rule():
    """Length is its own damage pattern: a run-on contains a word from most
    lists, so the question is whether anything claims it that should not.
    """
    said = RUN_ONS[0]
    watcher = lesson()
    assert watcher.answer(said) is False
    assert not is_noise(said)
    # And it reaches the corrections intact rather than being truncated.
    route, _why = correct(Route(Intent.SHOW, False, False, "model"), said)
    assert route.intent is Intent.SHOW


@pytest.mark.parametrize("said", [
    "stop", "stop it", "never mind", "forget it", "cancel",
])
def test_stopping_is_never_swallowed_by_the_loose_matching(said):
    """The loose mid-lesson rules go LAST for this reason. "Stop" has to end
    a lesson, not advance it, and it is one word of exactly the shape the
    acknowledgement rule accepts.
    """
    watcher = lesson()
    assert watcher.answer(said) is True
    assert watcher.walkthrough is None, said


@pytest.mark.parametrize("said", [
    "i cant find it", "i can't find it", "where is it", "i'm lost",
])
def test_being_stuck_is_never_read_as_having_finished(said):
    """The worst confusion available here: somebody who cannot find the
    thing being told "good, now do the next one".
    """
    watcher = lesson()
    assert watcher.answer(said) is True
    assert watcher.walkthrough.index == 0, said


# --- the third live run -----------------------------------------------------
#
# "Open blender." routes ACT now, and teach_me_this fired with a real Blender
# procedure. What broke instead was everything said BACK to it mid-lesson.


@pytest.mark.parametrize("said", [
    "Yes, sir.",
    "ok cool",
    "yeah alright",
    "accha done",
    "haan done",
])
def test_a_short_agreement_advances_however_it_is_dressed(said):
    """"Yes, sir." was answered with "i'm not quite sure what you mean" while
    the terminal printed "lesson: step 1 of 4" on the same turn.
    """
    watcher = lesson()
    assert watcher.answer(said) is True, said
    assert watcher.walkthrough.index == 1


@pytest.mark.parametrize("said", [
    "open notepad now",
    "show me now",
    "close it done",
    "minimize it ok",
])
def test_a_short_sentence_with_an_ACTION_is_never_an_agreement(said):
    """Written as ends-with-an-acknowledgement, the rule let "open notepad
    now" through - "now" is an acknowledgement and it is three words, so a
    lesson would have advanced instead of Notepad opening.
    """
    watcher = lesson()
    assert watcher.answer(said) is False, said
    assert watcher.walkthrough.index == 0


@pytest.mark.parametrize("said", ["What?", "huh", "sorry", "pardon"])
def test_not_catching_the_step_asks_for_it_again(said):
    """A bare "what?" mid-lesson is somebody who missed the instruction, not
    somebody asking a new question. Live it got "i'm here to help, but i need
    a bit more information. what's on your screen?"
    """
    watcher = lesson()
    assert watcher.answer(said) is True, said
    assert watcher.walkthrough.index == 0, "it must NOT advance"
    assert watcher.said_lines, "it has to say the step again"


def test_the_step_is_reworded_rather_than_repeated_verbatim():
    """Hearing the identical sentence back is how a person concludes they are
    talking to a recording.
    """
    watcher = lesson()
    first = watcher.walkthrough.say(
        __import__("meow.agent.walkthrough", fromlist=["Progress"]).Progress.ARRIVED)
    watcher.said_lines.clear()
    watcher.answer("What?")
    assert watcher.said_lines[0] != first


# --- two turns on one thread, which bricks the session ----------------------
#
# Live: "Open." started a turn; "RFO." arrived 2.9 seconds later while that
# turn was about to ask for confirmation; both ran on the same LangGraph
# thread. An assistant message with tool_calls was followed by a human
# message instead of the tool result, the API rejected it - and then rejected
# EVERY later turn on the same call_id, because the broken messages stay in
# the checkpoint. The session never recovered.


def test_the_interleaved_turns_400_is_recognised():
    """Narrow on purpose: a fresh thread throws the conversation away, so it
    must not happen for a rate limit or a bad argument.
    """
    from meow.agent.harness import Harness

    poisoned = (
        "Error code: 400 - {'error': {'message': \"An assistant message with "
        "'tool_calls' must be followed by tool messages responding to each "
        "'tool_call_id'. The following tool_call_ids did not have response "
        "messages: call_u4PLA1frk5WnT4NUxIbw4HPV\"}}")
    assert Harness._thread_is_poisoned(RuntimeError(poisoned))


@pytest.mark.parametrize("message", [
    "Error code: 429 - rate limit exceeded",
    "Error code: 400 - invalid 'messages[0].role'",
    "Connection reset by peer",
    "Error code: 400 - unknown parameter 'display_width'",
])
def test_an_ordinary_failure_does_not_throw_the_conversation_away(message):
    from meow.agent.harness import Harness

    assert not Harness._thread_is_poisoned(RuntimeError(message))


def test_a_fresh_thread_has_a_new_name_every_time():
    """The checkpointer keys on the name, so reusing it would resume the
    poisoned state it was abandoned for.
    """
    from meow.agent.harness import Harness

    harness = Harness.__new__(Harness)
    harness._thread_name = "session"
    harness._threads_abandoned = 0

    names = {harness.start_a_fresh_thread() for _ in range(3)}
    assert len(names) == 3
    assert "session" not in names

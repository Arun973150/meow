"""Pressing, typing and pointing at what is on screen.

These are the tools the whole project exists to make safe. Every one
of them resolves a NAME against the UIA digest rather than taking a
coordinate, so the model cannot produce a wrong pixel - it can only ask for a
control that does or does not exist. The ablation is what says that matters:
vision scored 0/30 at locating controls and UIA 30/30.

Every tool here closes over the harness, which owns the digest, the outbox,
the reader and the record of what ran. `build` returns them in the order the
agent should see them.
"""

from __future__ import annotations

from langchain_core.tools import tool

from .. import actions, apps, verify
from .support import (
    LAUNCH_SECONDS,
    SETTLE_SECONDS,
    WHOLE_DESKTOP_SHORTCUTS,
    where_on_screen,
)
from ..actions import Outcome
from ..uia import digest_foreground
from .record import ToolRun


def build(harness) -> list:
    """The tools in this module, bound to one harness."""

    @tool
    def list_controls() -> str:
        """Re-read the controls on screen, after something has changed."""
        harness.digest = digest_foreground()
        if harness.digest is None:
            return "No window is in the foreground."
        return harness.digest.to_prompt()


    @tool
    def click_control(name: str) -> str:
        """Press a control on screen. Use its exact name from the list."""
        refusal = harness._explaining(f"clicking {name}")
        if refusal:
            return refusal
        target = harness._resolve(name)
        if target is None:
            return harness._no_such_control(name)
        before = verify.look()
        outcome = actions.invoke(target, harness._gated("click_control", name))
        harness.runs.append(ToolRun("click_control", name, outcome, target))
        if not outcome.ok:
            return outcome.detail
        # A short settle. A button's effect is not on screen the instant
        # Invoke returns - a dialog takes a moment to appear and a window
        # a moment to close - and checking too early reads the old world
        # and reports a working click as unverified.
        verify.wait_until(
            lambda: verify.anything_changed(
                before, verify.look()).happened is True,
            SETTLE_SECONDS)
        verdict = verify.anything_changed(before, verify.look(),
                                          f"pressing {name}")
        return f"{outcome.detail}. {verdict.phrase()}"


    @tool
    def point_at_control(name: str) -> str:
        """Move the pointer to a control to show where it is. Presses nothing."""
        target = harness._resolve(name)
        if target is None:
            return harness._no_such_control(name)
        outcome = actions.point_at(target)
        harness.runs.append(ToolRun("point_at_control", name, outcome, target))
        if not outcome.ok:
            return outcome.detail
        # WHERE it is, in words, because the model has the coordinates and
        # no sense of them. Without this it pointed correctly at Minimize
        # in the top right and told the user it was at the bottom right.
        place = where_on_screen(target)
        return (f"{outcome.detail}. It is {place}." if place
                else outcome.detail)


    @tool
    def type_text(text: str) -> str:
        """Type text into whatever currently has keyboard focus."""
        refusal = harness._explaining("typing")
        if refusal:
            return refusal
        before = verify.look()
        outcome = actions.type_text(text, harness._gated("type_text", text))
        harness.runs.append(ToolRun("type_text", text, outcome))
        if not outcome.ok:
            return outcome.detail
        verify.wait_until(
            lambda: verify.typed(before, verify.look(),
                                 text).happened is True,
            SETTLE_SECONDS)
        verdict = verify.typed(before, verify.look(), text)
        return f"{outcome.detail}. {verdict.phrase()}"


    @tool
    def open_app(name: str) -> str:
        """Open an application by name, such as chrome, word or notepad.

        Use when what the user wants is not on screen at all.
        """
        refusal = harness._explaining(f"opening {name}")
        if refusal:
            return refusal

        # Windows' own pages first. There is no Settings.exe, so the
        # installed-application search cannot find Settings and WILL find
        # something else with the word in it - on this machine, WSL
        # Settings, which it opened twice while saying it had not.
        shell = apps.find_shell_target(name)
        if shell is not None:
            if not harness._gated("open_app", name)(f"open {name}?"):
                refused = Outcome(False, f"The user declined, so {name} "
                                         f"was not opened.", refused=True)
                harness.runs.append(ToolRun("open_app", name, refused))
                return refused.detail
            before = verify.look()
            started = apps.open_shell_target(shell)
            harness.runs.append(ToolRun(
                "open_app", name,
                Outcome(started, f"opened {name}" if started
                        else f"could not open {name}", method="shell")))
            if not started:
                return f"Could not open {name}."
            harness._wait_for_app(name)
            harness.digest = digest_foreground()
            verdict = verify.opened(before, verify.look(), name)
            return f"Opened {name}. {verdict.phrase()}"

        application = apps.find_application(name)
        if application is None:
            installed = [a.name for a in apps.list_applications()]
            near = [a for a in installed
                    if any(w in a for w in name.lower().split() if len(w) > 2)]
            suggestion = (f" Closest installed: {', '.join(near[:6])}."
                          if near else "")
            return f"No application called {name!r} is installed.{suggestion}"

        if not harness._gated("open_app", application.name)(
                f"open {application.name}?"):
            # Recorded, not just returned. The planner decides whether a
            # step succeeded by looking at these, and an early return with
            # no record made a refused step read as a completed one.
            refusal = Outcome(False, f"The user declined, so "
                                     f"{application.name} was not opened. "
                                     f"Nothing is wrong.", refused=True)
            harness.runs.append(ToolRun("open_app", name, refusal))
            return refusal.detail

        before = verify.look()
        started = apps.launch(application)
        outcome = Outcome(started,
                          f"opened {application.name}" if started
                          else f"could not open {application.name}",
                          method="launch")
        harness.runs.append(ToolRun("open_app", name, outcome))
        if started:
            # An application takes a moment to put a window up, and the
            # control list is read from whatever is in front. Reading it
            # too early returns the OLD window and the next tool call acts
            # on the wrong application entirely. Polled rather than slept
            # through: the point is that the window is READY, and a fixed
            # sleep answers a different question.
            harness._wait_for_app(application.name)
            harness.digest = digest_foreground()
            verdict = verify.opened(before, verify.look(), application.name)
            return f"{outcome.detail}. {verdict.phrase()}"
        return outcome.detail


    @tool
    def switch_to_window(name: str) -> str:
        """Bring an already-open window to the front, by name or app."""
        window = apps.find_window(name)
        if window is None:
            open_now = [w.describe() for w in apps.list_windows()[:8]]
            return (f"No open window matches {name!r}. Open windows: "
                    f"{'; '.join(open_now)}")

        came_forward = apps.focus_window(window)
        outcome = Outcome(came_forward,
                          f"switched to {window.title[:50]}" if came_forward
                          else f"could not bring {window.title[:40]} forward",
                          method="focus")
        harness.runs.append(ToolRun("switch_to_window", name, outcome))
        if came_forward:
            verify.wait_until(
                lambda: verify.switched(verify.look(),
                                        window.title).happened is True,
                0.6)
            harness.digest = digest_foreground()
            # focus_window returning True is not proof. Windows refuses
            # foreground changes from a process that is not already in
            # front, and the refusal is silent - it flashes the taskbar
            # button instead, which reports as success here.
            verdict = verify.switched(verify.look(), window.title)
            return f"{outcome.detail}. {verdict.phrase()}"
        return outcome.detail


    @tool
    def list_open_windows() -> str:
        """What windows are open, to switch between."""
        windows = apps.list_windows()
        if not windows:
            return "No windows are open."
        return "Open windows: " + "; ".join(w.describe() for w in windows[:14])


    @tool
    def press_keys(keys: str) -> str:
        """Press a keyboard shortcut, such as "ctrl+t" or "enter".

        Use ONLY for things with no clickable control - opening a new tab,
        submitting a search, moving focus to an address bar. If a button
        on screen does the job, click that instead.
        """
        refusal = harness._explaining(f"pressing {keys}")
        if refusal:
            return refusal

        # A whole-desktop shortcut in answer to a question about one
        # window. win+m minimises EVERYTHING the user has open, and the
        # button that minimises the window they named is in the digest.
        # Sent back rather than asked about, because "press win+m?" is a
        # question nobody can answer usefully without knowing it applies
        # to every window.
        instead = WHOLE_DESKTOP_SHORTCUTS.get(keys.strip().lower())
        if instead and harness.digest is not None:
            control = harness.digest.find(instead)
            if control is not None:
                return (f"Not pressed. {keys} affects every window on the "
                        f"desktop, and this is about one. There is a "
                        f"{control.name!r} control on screen - use "
                        f"click_control with that name.")
        before = verify.look()
        outcome = actions.press_shortcut(keys, harness._gated("press_keys", keys))
        harness.runs.append(ToolRun("press_keys", keys, outcome))
        if not outcome.ok:
            return outcome.detail
        verify.wait_until(
            lambda: verify.anything_changed(
                before, verify.look()).happened is True,
            SETTLE_SECONDS)
        verdict = verify.anything_changed(before, verify.look(),
                                          f"pressing {keys}")
        return f"{outcome.detail}. {verdict.phrase()}"


    return [
        list_controls,
        click_control,
        point_at_control,
        type_text,
        open_app,
        switch_to_window,
        list_open_windows,
        press_keys,
    ]

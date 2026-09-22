"""Reading accounts, and DRAFTING - never sending.

Read and draft only. There is deliberately no send tool in this
module or any other the harness loads: the harness holds the screen, which is
private data AND untrusted content, so an outbound channel here would close
the trifecta in one move. The application sends, after a person has approved a
draft by id. See meow/connectors/.

A to-do is the one write that is a plain tool rather than a draft, because it
has no recipient - nothing a hostile page said can choose where it goes.

Every tool here closes over the harness, which owns the digest, the outbox,
the reader and the record of what ran. `build` returns them in the order the
agent should see them.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..actions import Outcome
from ..connectors.drafts import spoken_email
from .record import ToolRun


def build(harness) -> list:
    """The tools in this module, bound to one harness."""

    @tool
    def read_mail(query: str = "is:unread") -> str:
        """Read recent email. Gmail search syntax, e.g. "from:priya",
        "is:unread", "subject:invoice", "newer_than:2d".

        Use for "what's in my inbox", "any mail from X", "what did Y say".
        """
        found = harness._reader().inbox(query)
        harness.runs.append(ToolRun("read_mail", query,
                                 Outcome(True, "read the inbox")))
        return found


    @tool
    def read_message(message_id: str) -> str:
        """One email in full, by the id shown in the inbox listing."""
        found = harness._reader().read_message(message_id)
        harness.runs.append(ToolRun("read_message", message_id,
                                 Outcome(True, "read a message")))
        return found


    @tool
    def my_agenda() -> str:
        """What is on the calendar. Use for "what's on today"."""
        found = harness._reader().agenda()
        harness.runs.append(ToolRun("my_agenda", "",
                                 Outcome(True, "read the calendar")))
        return found


    @tool
    def explain_video(url: str) -> str:
        """Fetch a YouTube video's captions so you can explain it.

        Use for "what is this video about", "summarise this video". Takes
        a URL or a video id.
        """
        found = harness._reader().video_transcript(url)
        harness.runs.append(ToolRun("explain_video", url,
                                 Outcome(True, "read the captions")))
        return found


    @tool
    def find_contact(name: str) -> str:
        """Somebody's email address, found in the user's own mail.

        ALWAYS try this before asking anyone to say an address out loud.
        A dictated address does not survive transcription and spelling it
        out is worse. Give the name as you heard it.
        """
        people = harness._reader().find_people(name)
        harness.runs.append(ToolRun(
            "find_contact", name,
            Outcome(bool(people), f"{len(people)} matched")))
        if not people:
            return (f"Nobody matching '{name}' in their mail. Say so, and "
                    "ask them to type it into the chat window rather than "
                    "say it - a spoken address does not survive.")
        lines = [f"{address}  ({display or 'no name'})"
                 for address, display in people[:5]]
        return ("Addresses, best first:" + chr(10)
                + chr(10).join(lines) + chr(10) + chr(10)
                + "Now call draft_reply with the FIRST one, exactly as "
                + "written. Do not ask which to use and do not ask "
                + "permission - finding the address was the missing "
                + "piece, and the draft is what they asked for. They "
                + "approve it by saying 'send it' once they have heard "
                + "the address read back.")


    @tool
    def draft_reply(to: str, subject: str, body: str) -> str:
        """Write an email and put it in the outbox. DOES NOT SEND IT.

        Use when asked to reply or write to somebody. The user reads the
        exact recipient and the exact body and decides. Say what you have
        drafted and who it is to; do not claim it was sent.
        """
        # A dictated address arrives with spaces through it and no @,
        # and passed through it failed at SEND - by which point the only
        # advice left was "say it without spaces", which is not something
        # the user controls. Fixed here, where the address is first seen.
        address = spoken_email(to)
        if not address:
            harness.runs.append(ToolRun("draft_reply", to,
                                     Outcome(False, "no usable address")))
            return (f"'{to}' is not an address that can be sent to, and "
                    "guessing one would send this to a stranger. Call "
                    "find_contact with the name instead. Do NOT ask them "
                    "to spell it out: single letters are dropped as noise "
                    "and 'go w d a r u n' is what comes back, so asking "
                    "again gets the same thing again.")

        draft = harness.outbox.add(
            harness._reader().compose_reply(address, subject, body,
                                         source=harness.transcript))
        harness.runs.append(ToolRun("draft_reply", to,
                                 Outcome(True, f"drafted {draft.id}")))
        # The id matters: approval is by id, so this is what the user is
        # approving when they say send it.
        heard = ("" if address == to.strip().lower()
                 else f" (heard as '{to}')")
        return (f"Drafted, NOT sent. Draft {draft.id}:" + chr(10)
                + draft.describe() + chr(10) + chr(10)
                + f"The address is {address}{heard}. READ IT BACK "
                + "character by character before anything is sent - it "
                + "was dictated, and a wrong one delivers to a stranger. "
                + "Then say what it says and that they can say 'send it' "
                + "or open the window to read it. Never say it was sent.")


    @tool
    def draft_event(title: str, when: str, hours: int = 1,
                    attendees: str = "") -> str:
        """Put an event in the user's REAL calendar. Does not create it yet.

        Use for "put X in my calendar", "book a meeting on Tuesday". This
        is their connected Google Calendar - do NOT open a calendar
        application on screen and do NOT say you cannot see their
        calendar. `when` is whatever they said: "the 25th of September",
        "tomorrow at 3".
        """
        made = harness._reader().compose_event(title, when, hours, attendees)
        if isinstance(made, str):
            harness.runs.append(ToolRun("draft_event", when,
                                     Outcome(False, "date not understood")))
            return made
        draft = harness.outbox.add(made)
        harness.runs.append(ToolRun("draft_event", title,
                                 Outcome(True, f"drafted {draft.id}")))
        return (f"Drafted, NOT created. Draft {draft.id}:" + chr(10)
                + draft.describe() + chr(10) + chr(10)
                + "Say the FULL day and time back - a spoken date is easy "
                + "to mishear and an event on the wrong day is not found "
                + "until the day. Then they say 'send it' to create it.")


    @tool
    def draft_slack(channel: str, text: str) -> str:
        """Write a Slack message for a channel. DOES NOT POST IT."""
        draft = harness.outbox.add(
            harness._reader().compose_message(channel, text))
        harness.runs.append(ToolRun("draft_slack", channel,
                                 Outcome(True, f"drafted {draft.id}")))
        return (f"Drafted, NOT posted. Draft {draft.id}:" + chr(10)
                + draft.describe() + chr(10) + chr(10)
                + "Say the channel and what it says. They post it by "
                + "saying 'send it'. Never say it has been posted.")


    @tool
    def check_weather(location: str) -> str:
        """The weather somewhere. Needs no login."""
        found = harness._reader().weather(location)
        harness.runs.append(ToolRun("check_weather", location,
                                 Outcome(True, "read the weather")))
        return found


    @tool
    def hacker_news(min_points: int = 100) -> str:
        """The Hacker News front page. Needs no login."""
        found = harness._reader().hacker_news(min_points)
        harness.runs.append(ToolRun("hacker_news", str(min_points),
                                 Outcome(True, "read the front page")))
        return found


    @tool
    def my_tasks() -> str:
        """The user's to-do list. Use for "what's on my list"."""
        found = harness._reader().my_tasks()
        harness.runs.append(ToolRun("my_tasks", "",
                                 Outcome(True, "read the list")))
        return found


    @tool
    def add_task(title: str, notes: str = "") -> str:
        """Add one thing to the user's to-do list.

        Goes straight in rather than becoming a draft: a task has no
        recipient, so there is no destination for anything to choose.
        """
        done = harness._reader().add_task(title, notes)
        harness.runs.append(ToolRun("add_task", title,
                                 Outcome(not done.startswith("Could not"),
                                         "added a task")))
        return done


    @tool
    def read_google_doc(url_or_id: str) -> str:
        """A Google Doc, by link or id."""
        found = harness._reader().read_document(url_or_id)
        harness.runs.append(ToolRun("read_google_doc", url_or_id[:40],
                                 Outcome(True, "read a document")))
        return found


    @tool
    def read_google_sheet(url_or_id: str, ranges: str = "A1:Z50") -> str:
        """A Google Sheet, by link or id."""
        found = harness._reader().read_spreadsheet(url_or_id, ranges)
        harness.runs.append(ToolRun("read_google_sheet", url_or_id[:40],
                                 Outcome(True, "read a spreadsheet")))
        return found


    return [
        read_mail,
        read_message,
        my_agenda,
        explain_video,
        find_contact,
        draft_reply,
        draft_event,
        draft_slack,
        check_weather,
        hacker_news,
        my_tasks,
        add_task,
        read_google_doc,
        read_google_sheet,
    ]

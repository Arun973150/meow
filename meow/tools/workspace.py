"""Files the cat made, and work it has already done.

Documents land in Documents/Meow rather than wherever the cursor
happens to be. `recall_task_results` is here because what a handed-over task
produced is the same kind of thing: something made earlier that the user may
now want.

Every tool here closes over the harness, which owns the digest, the outbox,
the reader and the record of what ran. `build` returns them in the order the
agent should see them.
"""

from __future__ import annotations

from langchain_core.tools import tool

from .. import documents
from ..actions import Outcome
from .record import ToolRun


def build(harness) -> list:
    """The tools in this module, bound to one harness."""

    @tool
    def make_document(name: str, heading: str,
                      paragraphs: list[str]) -> str:
        """Write a Word document and save it. Give real paragraphs, not a
        topic - compose the text yourself first.
        """
        refusal = harness._explaining("writing a document")
        if refusal:
            return refusal
        made = documents.make_docx(name, heading, paragraphs)
        harness._last_document = made
        harness.runs.append(ToolRun("make_document", name,
                                 Outcome(True, made.describe(),
                                         method="docx")))
        return f"{made.describe()} in Documents/Meow."


    @tool
    def make_spreadsheet(name: str, headers: list[str],
                         rows: list[list[str]]) -> str:
        """Write a spreadsheet and save it. headers is the first row;
        rows is the data, each one the same length as headers.
        """
        made = documents.make_xlsx(name, headers, rows)
        harness._last_document = made
        harness.runs.append(ToolRun("make_spreadsheet", name,
                                 Outcome(True, made.describe(),
                                         method="xlsx")))
        return f"{made.describe()} in Documents/Meow."


    @tool
    def make_slides(name: str, title: str,
                    slide_titles: list[str],
                    slide_bullets: list[str]) -> str:
        """Write a slide deck and save it.

        slide_titles and slide_bullets line up one to one; each entry in
        slide_bullets is that slide's points separated by " | ".
        """
        slides = [
            {"title": slide_title,
             "bullets": [b.strip() for b in bullets.split("|") if b.strip()]}
            for slide_title, bullets in zip(slide_titles, slide_bullets)
        ]
        made = documents.make_pptx(name, title, slides)
        harness._last_document = made
        harness.runs.append(ToolRun("make_slides", name,
                                 Outcome(True, made.describe(),
                                         method="pptx")))
        return f"{made.describe()} in Documents/Meow."


    @tool
    def open_last_document() -> str:
        """Open the file that was just written."""
        if harness._last_document is None:
            return "Nothing has been written yet."
        opened = documents.open_document(harness._last_document)
        return (f"Opened {harness._last_document.path.name}." if opened
                else f"Could not open {harness._last_document.path.name}.")


    @tool
    def recall_task_results() -> str:
        """What background tasks have found or produced.

        Use when asked to paste, use or refer to the results of something
        that ran in the background.
        """
        getter = getattr(harness, "task_results", None)
        results = getter() if getter else []
        if not results:
            return "No background task has produced anything yet."
        return "\n\n".join(f"[{title}]\n{body}" for title, body in results)


    return [
        make_document,
        make_spreadsheet,
        make_slides,
        open_last_document,
        recall_task_results,
    ]

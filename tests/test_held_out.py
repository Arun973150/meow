"""Scoring against targets the strategies did not choose.

No model, no network, no Windows: the `uia` condition is pure string matching
over a digest read from a file, which is exactly the part worth testing
automatically. The `uia+model` and `vision` conditions need a live key and are
exercised by `meow evaluate --labelled`.
"""

from __future__ import annotations

import json

import pytest

from meow.diagnostics import held_out
from meow.diagnostics.label import Label

DIGEST = {
    "app": "Notepad.exe",
    "title": "Untitled - Notepad",
    "regime": "RICH",
    "total_found": 3,
    "elements": [
        {"name": "Close", "role": "Button", "left": 900, "top": 0,
         "right": 940, "bottom": 30, "enabled": True},
        {"name": "Minimize", "role": "Button", "left": 820, "top": 0,
         "right": 860, "bottom": 30, "enabled": True},
        {"name": "Text editor", "role": "Edit", "left": 0, "top": 40,
         "right": 940, "bottom": 600, "enabled": True},
    ],
}


@pytest.fixture
def labelled(tmp_path, monkeypatch):
    """A labelled set on disk, with the folder pointed somewhere disposable."""
    monkeypatch.setattr(held_out, "folder", lambda: tmp_path, raising=False)
    from meow.diagnostics import label as label_module

    monkeypatch.setattr(label_module, "folder", lambda: tmp_path)
    (tmp_path / "digest-000.json").write_text(json.dumps(DIGEST),
                                              encoding="utf-8")

    def make(description, point, nearest="Close"):
        return Label(description=description, point=point, app="Notepad.exe",
                     window="Untitled - Notepad",
                     digest_file="digest-000.json", nearest_uia_name=nearest)

    return make


def test_a_hit_is_the_rectangle_containing_the_marked_point(labelled):
    """Not distance to a centre. A person marked a spot; either the returned
    control covers it or it does not.
    """
    report = held_out.run([labelled("close", (920, 15))], strategies=("uia",))
    assert report.outcomes[0].hit


def test_a_description_sharing_no_words_is_not_found(labelled):
    """The honest case, and the one the sampled ablation could never produce.

    "where i type" shares nothing with "Text editor", so fuzzy name matching
    has nothing to work with. Asking UIA for a control's own name back is what
    made the old score 30/30; this is what happens when the words are a
    person's instead.
    """
    report = held_out.run([labelled("where i type", (400, 300),
                                    nearest="Text editor")],
                          strategies=("uia",))
    assert not report.outcomes[0].hit
    assert not report.outcomes[0].answered


def test_a_miss_on_the_wrong_control_is_measured_not_dropped(labelled):
    """A wrong answer and no answer are different failures. Reporting them as
    one number hides which strategy is guessing.
    """
    report = held_out.run([labelled("minimise it", (920, 15))],
                          strategies=("uia",))
    outcome = report.outcomes[0]
    if outcome.answered:
        assert outcome.distance is not None


def test_in_digest_is_recorded_but_never_given_to_a_strategy(labelled):
    """It answers "was the target even in the tree?", which separates UIA
    failing to find something it could see from being asked for something it
    never had. Those look identical in a hit rate and mean opposite things.
    """
    report = held_out.run([labelled("close", (920, 15))], strategies=("uia",))
    assert report.outcomes[0].in_digest is True

    absent = held_out.run([labelled("close", (920, 15), nearest="")],
                          strategies=("uia",))
    assert absent.outcomes[0].in_digest is False


def test_an_empty_set_scores_nothing_rather_than_crashing(labelled):
    report = held_out.run([], strategies=("uia",))
    assert report.labels == 0
    assert report.outcomes == []


def test_the_summary_counts_hits_out_of_attempts(labelled):
    report = held_out.run(
        [labelled("close", (920, 15)),
         labelled("where i type", (400, 300), nearest="Text editor")],
        strategies=("uia",))
    summary = report.summary()
    assert "1/2" in summary
    assert "hand-labelled" in summary

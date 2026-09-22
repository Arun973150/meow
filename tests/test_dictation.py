"""Things people say out loud that arrive mangled.

An address and a date are the two places where the transcriber's output is not
merely imperfect but structurally wrong - spaces through the middle of an
address, no year on a date - and where being confidently wrong sends mail to a
stranger or books a meeting nobody attends.

Every transcript in here is one the microphone actually produced.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from meow.connectors.drafts import spoken_email
from meow.connectors.reader import _addresses, _drive_id, spoken_datetime

NOW = datetime(2026, 9, 22, 14, 30)


@pytest.mark.parametrize("heard, expected", [
    # Straight from a live session.
    ("Gauda Arun 032 gmail.com", "gaudaarun032@gmail.com"),
    ("gowda arun032@gmail.com", "gowdaarun032@gmail.com"),
    # How people actually say it.
    ("gowda arun 032 at gmail dot com", "gowdaarun032@gmail.com"),
    ("arun at the rate gmail dot com", "arun@gmail.com"),
    # "at" inside a name must not take the domain with it.
    ("pat at gmail.com", "pat@gmail.com"),
    ("Priya.Sharma@work.co.uk", "priya.sharma@work.co.uk"),
    ("jsrija6955@gmail.com.", "jsrija6955@gmail.com"),
])
def test_a_dictated_address_is_repaired(heard, expected):
    assert spoken_email(heard) == expected


@pytest.mark.parametrize("heard", ["send it to whoever", "", "just arun"])
def test_an_address_is_refused_rather_than_guessed(heard):
    # An address wrong in a way nobody notices delivers to a stranger while
    # the sender believes it arrived.
    assert spoken_email(heard) == ""


@pytest.mark.parametrize("header, expected", [
    ("Arun Gowda <gowdaarun032@gmail.com>",
     [("Arun Gowda", "gowdaarun032@gmail.com")]),
    # One greedy pattern for the display name ate into the address, inventing
    # a person called "no-repl" at "y@accounts.google.com".
    ("no-reply@accounts.google.com", [("", "no-reply@accounts.google.com")]),
    ("Google <no-reply@accounts.google.com>",
     [("Google", "no-reply@accounts.google.com")]),
    ("nothing here", []),
])
def test_addresses_are_parsed_out_of_a_header(header, expected):
    assert _addresses(header) == expected


@pytest.mark.parametrize("said, expected", [
    ("25th of September", "Fri 25 Sep 2026 09:00"),
    ("the 25th of September at 3pm", "Fri 25 Sep 2026 15:00"),
    ("tomorrow", "Wed 23 Sep 2026 09:00"),
    ("tomorrow at 10am", "Wed 23 Sep 2026 10:00"),
    ("october 3rd at 2:30pm", "Sat 03 Oct 2026 14:30"),
])
def test_a_spoken_date_resolves(said, expected):
    got = spoken_datetime(said, now=NOW)
    assert got is not None
    assert got.strftime("%a %d %b %Y %H:%M") == expected


def test_a_date_already_past_rolls_forward():
    # Nobody schedules into last week.
    got = spoken_datetime("20th of September", now=NOW)
    assert got is not None and got.year == 2027


@pytest.mark.parametrize("said", ["sometime soonish", "", "whenever"])
def test_a_date_is_refused_rather_than_guessed(said):
    # An event on the wrong day is worse than no event: nobody finds out
    # until the day.
    assert spoken_datetime(said, now=NOW) is None


@pytest.mark.parametrize("link, expected", [
    ("https://docs.google.com/document/d/1AbC_deF/edit?usp=sharing", "1AbC_deF"),
    ("https://docs.google.com/spreadsheets/d/9XyZ/edit#gid=0", "9XyZ"),
    ("1AbC_deF", "1AbC_deF"),
])
def test_a_drive_id_comes_out_of_a_link(link, expected):
    assert _drive_id(link) == expected

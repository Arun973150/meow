# put something in the calendar

when: book, booking, schedule, reschedule, appointment, meeting, calendar
event, add to my calendar, put in my calendar, remind me on, block time,
set up a call, interview on

The calendar is a connected Google account, not an application on screen. Do
not open a calendar program and do not say you cannot see their calendar -
`draft_event` reaches the real one.

`draft_event(title, when, hours, attendees)` where `when` is whatever they
actually said: "the 25th of September", "tomorrow at 3", "next friday".
Spoken dates carry no year, so it resolves one and **reads the whole day and
time back** before anything is created. Say the weekday too: "friday the
twenty-fifth at nine" is checkable by ear, "the twenty-fifth" is not.

It is a DRAFT. Nothing is in the calendar until they say "send it". An event
takes attendees, and an attendee is an address - so it goes through the same
approval path as mail, for the same reason.

If the date cannot be pinned down - "sometime soonish", "next week maybe" -
ask for a day rather than guessing. An event on the wrong day is worse than
no event: nobody finds out until the day.

To read what is already there, `my_agenda`. Checking before booking is worth
the extra second when somebody says "am I free on Thursday".

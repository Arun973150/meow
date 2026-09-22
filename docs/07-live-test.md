# Live test — the whole thing, out loud

Everything below is done by talking. It takes about twenty minutes and touches
every part built so far: routing, memory, grounding, guide mode, research,
background agents, the chat window, human-in-the-loop, verification and panic.

Run it after any change that touches the voice loop. `scripts/smoke.py` and
`scripts/stress.py` cover what can be checked without a microphone; this covers
what cannot.

**Before you start**

```
python scripts/stress.py          # 52 checks, ~20s
python scripts/smoke.py           # the app survives real frames, ~10s
python scripts/meow.py            # then talk
```

Have Notepad closed, a browser open, and the terminal visible so you can read
the log while it runs. Keep an eye on the **top right of the screen** — that is
where agent icons appear.

Each step says what to say, what should happen, and **what would be a bug**.
The bugs listed are real ones that have happened, not hypotheticals.

---

## 1 — It hears you, in English

Say: **"hey can you open notepad for me"**

- The transcript comes back in **Latin script**
- Route is `act`
- Notepad opens

**Bug if:** the transcript is Devanagari or another script. The English model
is meant to be pinned; a multilingual model transliterates accented English and
the harness then gets a sentence it cannot act on.

**Bug if:** it asks permission. You named Notepad yourself, and repeating your
own sentence back is how a prompt becomes furniture.

---

## 2 — Typing is correct, and fast

Say: **"write a paragraph about large language models"**

- It composes several sentences and they appear in Notepad
- It takes about a second, not half a minute
- The text is **exactly right** — read it

**Bug if:** characters are wrong. `hello from the probe` once arrived as
`hello rrom rrrrrrobe`. Long text goes through the clipboard now; short text
types at 20 characters per second, which is the only speed that does not
corrupt.

**Bug if:** your clipboard is different afterwards. It is borrowed and put
back — check by pasting somewhere.

---

## 3 — It remembers the turn before

Say: **"minimise it"** then, after it does, **"put it back"**

- "it" resolves to Notepad both times
- The second sentence works without naming anything

**Bug if:** it asks what you mean. The harness keeps one thread for the whole
session; a fresh thread per turn meant every turn started blank.

---

## 4 — Asking HOW gets instructions, not an action

Say: **"how do i turn on dark mode"**

- It reads a route out: *settings, then personalization, then colors*
- It points if any of those is on screen
- **Nothing is clicked, opened or typed**

Then say: **"where is the bluetooth setting"** — same shape of answer.

**Bug if:** it changes anything. SHOW turns put the harness in guide mode where
every acting tool refuses. This is enforced in the tools, not asked for in the
prompt.

**Bug if:** it invents a location instead of looking one up.

---

## 5 — And asking to DO it still does it

Say: **"open settings"**

- It opens. The distinction is whether you want it explained or done, and
  "how do i" versus a plain instruction is the whole difference.

**Bug if:** it explains instead of acting. Guide mode has leaked into act.

---

## 6 — Noise is ignored

Say, each on its own: **"oh"** … **"hmm"** … a single **"haan"** or any one
word in another language.

- The log prints `(ignored: Oh.)` and nothing happens

**Bug if:** any of them starts work. `"Oh."` once got routed to plan and opened
its own background window. One word is not an instruction in any language.

---

## 7 — Research, with an agent icon and sources

Say: **"research gpu prices in india and put it in a spreadsheet"**

Watch for all of these:

- It says **"i am on it"** and goes back to listening immediately
- A **magnifier icon appears at the top right**, spinning
- The icon **stays put** if the cat moves
- It finishes in roughly half a minute and names the file
- The icon **disappears** when it finishes

**Bug if:** the icon follows the cat around. It is pinned deliberately — an
icon that moves cannot be clicked without chasing it.

**Bug if:** the icon sits on a window's close button. It starts 96px down for
that reason; these overlays intercept clicks rather than passing them on.

---

## 8 — While it runs, ask what it is doing

While the research is still going, say: **"what is it doing"**

- The cat answers from shared memory — *researching gpu prices, reading the
  third source* or similar
- The task is **not** interrupted

**Bug if:** it says it does not know. Memory is shared between the voice loop,
the harness and every task.

---

## 9 — The chat window

Click the **agent icon** while it is running, or the cat in the system tray.

- A window opens **on that agent's conversation**
- Messages are bubbles: you on the right, meow on the left, the agent in a
  third colour
- The sidebar lists every conversation with its own icon
- Search finds anything ever said

When the task has finished, find the row that says **"made this — click to
open"** and click it.

- The spreadsheet opens
- It has a **source column** with real domains in it

**Bug if:** the agent's conversation contains things the foreground said. They
are separate conversations.

**Bug if:** the spreadsheet has "not specified" everywhere. That means the
search found the shape of your sentence rather than its subject.

---

## 10 — Human in the loop, for something that matters

Say: **"open notepad and delete everything in it"**

- It hands over, an icon appears
- The icon turns **amber** and **stops spinning**
- A question appears in that agent's conversation with **yes** and **no**
- The task is stopped, waiting — not failed

Click **no**.

- The task carries on and reports what it could not do

Then try it again and click **yes** — it should proceed from exactly where it
stopped.

**Bug if:** it asks you out loud. A background task must never interrupt the
voice loop.

**Bug if:** it asks about ordinary steps — opening an app, typing, saving.
Delegating a job is consent to the ordinary steps of doing it; only things that
are hard to undo are worth stopping for.

**Bug if:** the icon disappears while it is waiting. Waiting is not finished.

---

## 11 — It does not claim what it did not check

Say: **"click the close button"** with something focused that has one.

- It reports whether the window actually closed, not that it tried

Say: **"type hello"** with nothing focused that can hold text.

- It refuses and says focus is somewhere that cannot hold text

**Bug if:** it says it did something and nothing happened. Every action is
checked against the accessibility tree afterwards, and the three answers are
yes, no, and **could not tell** — the third one being said out loud is correct,
not a failure.

---

## 12 — Panic

Start something long — **"research the history of computing and make a deck"** —
and while it is running press **Pause**.

- Everything stops at once
- No model call, no network, nothing on the way out

**Bug if:** anything keeps going, or the key is slow. The panic path is local
by design.

---

## 13 — It comes back

Quit with Ctrl+C and start it again.

- The chat window still has every conversation
- Finished agents are not showing icons

**Bug if:** an agent icon appears for work that stopped days ago. Conversations
left open by a crash are closed at startup — a task cannot be running in a
process that no longer exists.

---

## What to send back

The terminal log is the useful part. Three things worth reading for:

- any reply **repeated** from an earlier turn
- any `Could not verify` line, and whether it was honest or just noise
- anything it claimed to do that it did not do

The third is the one that matters most. The others are annoyances; that one is
the cat being wrong about the world.

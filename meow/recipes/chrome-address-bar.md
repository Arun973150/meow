# go to a website in Chrome

when: chrome, browser, website, url, address bar, navigate, open site,
search web, youtube, google search, go to, visit, open a page, profile,
profiles, profile picker,
which profile, whos using chrome

## Searching for something is FOUR things, in this order

1. Ctrl+T - a new tab
2. Ctrl+L - the cursor into the address bar
3. type the search words or the address
4. Enter

Do all four without stopping between them. None of them can lose any work, so
there is nothing to check in the middle, and a sequence interrupted halfway is
a tab sitting open with nothing in it.

A new tab already has the address bar focused, so step 2 is usually redundant -
and it is harmless, which is why it stays in the list rather than being a thing
to reason about each time. The failure to avoid is the opposite one: typing
before anything is focused, which goes nowhere and reports nothing changed.

"Search youtube.com", "search for campus x" and "go to spotify" are all this
sequence. Whatever follows the word search is what gets typed.

Ctrl+L puts the cursor in the address bar and selects whatever is already
there, so the next thing typed replaces it. That is more reliable than clicking
the bar, which sometimes places a cursor mid-text instead of selecting.

Type the address, then press Enter. Typing alone does nothing — Chrome shows a
suggestion list and waits.

For a new tab first, Ctrl+T. A new tab already has the address bar focused, so
Ctrl+L after it is harmless but unnecessary.

## Chrome may not be browsable the moment it opens

**"Who's using Chrome?" is a profile picker, not a browser window.** With more
than one profile, Chrome opens on a grid of avatars and there is no address
bar, no tab strip and nothing to type into. Ctrl+T does nothing. Ctrl+L does
nothing. Typing goes nowhere. Every one of those reports "nothing changed
visibly" — correctly, because nothing did.

Check before reaching for a shortcut. If the control list holds profile names,
or a **Guest mode** button, or the window title is not a page title, this is
the picker. Click a profile first; the browser window that follows is the one
that takes Ctrl+L.

A profile is identified by its NAME in the tree, not by its picture. Somebody
looking at the screen will say "the watermelon one" or "the green A", because
that is what distinguishes them visually — and no control is called
"watermelon". Read the names out and ask which, rather than matching something
that merely sounds close: the profiles here include several called "Arun".

The same is true after an update ("Chrome has been updated, relaunch") and on
a fresh profile's welcome screen. Opening Chrome is not the same as having
somewhere to type.

## YouTube

`youtube.com` in the address bar, or `youtube.com/results?search_query=...`
to land directly on a search. Going to the site and then typing into its own
search box needs the page to have finished loading first, and the address bar
does not.

Note that the connected YouTube account is a different thing entirely — it
reads captions and video details through the API, and cannot browse. A
request to "open YouTube and search for X" is a desktop job.

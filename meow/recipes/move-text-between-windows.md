# copy text from one place to another

when: copy that into, paste it into, move it to, put that in, transfer,
copy from, take this and, into notepad, into word, into excel, from chrome to

Two windows means two `switch_to_window` calls, and the order matters.

1. `switch_to_window` to the source, and read what is there. `list_controls`
   shows what the window offers; a document's text usually sits in one large
   Edit control.
2. `switch_to_window` to the destination and make sure something that can
   hold text has focus. Launching an application does NOT give focus to its
   text area - a freshly opened Notepad has left focus on a button, on a
   group, and once on an entirely unrelated window.
3. `type_text` then puts it in.

**Long text goes through the clipboard, and that is already handled.** Over a
couple of hundred characters, typing corrupts: at 90 characters per second
Notepad received "hello rrom rrrrrrobe" for "hello from the probe". The user's
clipboard is borrowed and put back.

**Check it arrived.** `SendInput` returning means the input queue accepted the
keystrokes, not that a field received them. If the verification says it could
not tell, say that - "i typed it but could not confirm it landed" is honest
and useful; claiming success is neither.

If the destination does not exist yet, `open_app` first and wait for it to be
walkable rather than typing immediately.

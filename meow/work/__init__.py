"""Work handed over, and how it is watched.

A task runs on its own thread with its own conversation and its own icon.
The icons are drawn beside the cat rather than in the system tray, because
Windows 11 hides a newly created tray icon per icon, forever - the
diagnostics said visible=True while nothing was on screen.
"""

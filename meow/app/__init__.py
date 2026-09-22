"""The running application: the voice loop and everything it drives.

Separate from the pieces it composes. `meow.platform` knows about Win32,
`meow.language` knows about words, `meow.tools` knows what the agent can do -
this package is the only one that knows about all of them at once, which is
what an application layer is for.
"""

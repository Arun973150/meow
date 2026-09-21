"""Meow - a voice-driven desktop companion for Windows.

Library warnings are filtered here rather than in a script, because they are
emitted when LangGraph is first imported - which happens on the way INTO this
package, before any script has run a line of its own. Filtering them in main()
was too late and printed them anyway.

Only library noise is silenced. Nothing raised by Meow is hidden.
"""

import warnings

# LangGraph prints a pending-deprecation notice about a serializer default on
# every import. It is addressed to whoever maintains this code, not to someone
# talking to a cat, and four lines of stack trace above the prompt on every
# launch teaches people to ignore the terminal - which is where the messages
# that DO matter appear.
warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
warnings.filterwarnings("ignore", message=r".*is in beta.*")
warnings.filterwarnings("ignore", category=PendingDeprecationWarning,
                        module=r"langgraph\..*")
warnings.filterwarnings("ignore", category=PendingDeprecationWarning,
                        module=r"langchain.*")

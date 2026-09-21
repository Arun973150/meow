"""The stop button - Phase 1.8.

Meow can move the pointer, press controls and type. There has to be a way to
make it stop that works instantly and always.

**Nothing on this path touches the network.** Invariant 5. No Jev, no model, no
websocket - a reflex that needs a round trip is not a reflex, and the moment a
user wants this is exactly the moment something else is already going wrong.
It is a `threading.Event` and a list of callbacks.

**Tripping it is cheap and untripping it is deliberate.** Panic stays latched
until it is explicitly reset, so an action already in flight cannot resume
because a flag happened to clear. Everything that acts checks `tripped` between
steps rather than only at the start.

**It is a single tap, never a hold.** Invariant 9, and more so here than
anywhere: someone reaching for a stop button is not in a state to manage a
two-key chord.

The default is Pause/Break. It is on every keyboard, almost nothing claims it,
and nobody presses it by accident - which matters more than being memorable,
because the cost of a false trip is an interrupted cat and the cost of a missed
one is an action nobody wanted.
"""

from __future__ import annotations

import threading
from typing import Callable

DEFAULT_PANIC_KEY = "pause"


class Panic:
    """A latched stop, with callbacks that run the moment it trips.

    Callbacks run on whichever thread trips the switch, so they must be quick
    and must not raise - a stop that half-works because one handler threw is
    worse than one that never existed. Each is wrapped.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._handlers: list[tuple[str, Callable[[], None]]] = []
        self._lock = threading.Lock()
        self.trips = 0

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    def on_panic(self, name: str, handler: Callable[[], None]) -> None:
        """Register something to stop. Order of registration is order of run."""
        with self._lock:
            self._handlers.append((name, handler))

    def trip(self) -> list[str]:
        """Stop everything. Returns the names of what was stopped.

        Safe to call repeatedly and from any thread. Re-running the handlers on
        an already-tripped switch is deliberate: a second press should still
        silence anything that started in between.
        """
        self._event.set()
        self.trips += 1

        stopped: list[str] = []
        with self._lock:
            handlers = list(self._handlers)
        for name, handler in handlers:
            try:
                handler()
                stopped.append(name)
            except Exception:  # noqa: BLE001 - one bad handler must not block
                stopped.append(f"{name} (failed)")
        return stopped

    def reset(self) -> None:
        """Clear the latch. Only ever called by a deliberate user action."""
        self._event.clear()

    def raise_if_tripped(self) -> None:
        if self._event.is_set():
            raise Stopped()

    def should_stop(self) -> bool:
        """Passed to long-running operations so they can give up mid-way."""
        return self._event.is_set()


class Stopped(RuntimeError):
    """Raised inside work that noticed the panic switch and gave up."""

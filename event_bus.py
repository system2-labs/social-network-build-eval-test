"""In-memory pub/sub event bus with typed subscriptions and per-handler error isolation.

Why this shape:
- ``publish`` is async-only. It's the single entry point that can drive sync
  handlers (plain call) and async handlers (await). A separate sync publish
  would need to invent an event loop for async handlers, which breaks inside
  a running loop -- two APIs buys nothing here.
- Subscriptions are typed by event class; dispatch uses ``isinstance`` so a
  base-class subscriber automatically receives subclass events with no MRO
  bookkeeping.
- Error isolation policy: each handler runs in its own ``try/except Exception``.
  A failing handler never blocks delivery to the rest; it surfaces as a
  ``HandlerResult.error`` plus a log line, not an exception out of publish.

Gotchas:
- Handlers are called then checked with ``isawaitable``, not pre-inspected:
  covers async def, callable classes with async ``__call__``, and wrappers
  returning coroutines. A sync handler whose return value is an awaitable gets
  awaited -- return values are otherwise unused by the bus.
- Only ``Exception`` is caught. BaseException subclasses (KeyboardInterrupt,
  SystemExit, asyncio.CancelledError) deliberately propagate -- a bus that
  swallows cancellation breaks task shutdown.
- Priority semantics: higher numeric value runs first; equal priorities keep
  subscription order (handles are monotonic, so sort key ``(-priority, handle)``
  is fully deterministic).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from itertools import count
from typing import Any, Callable

logger = logging.getLogger("event_bus")


@dataclass(frozen=True)
class HandlerResult:
    """Outcome of running one subscription for one publish.

    ``error`` is None on success; otherwise it holds the live exception
    instance (not re-wrapped), so callers can identity-check or type-test it.
    """

    handle: int
    error: BaseException | None = None


@dataclass(frozen=True)
class _Subscription:
    handle: int
    event_type: type
    handler: Callable
    priority: int


class EventBus:
    """In-memory pub/sub bus.

    Subscribe with a class; publish instances of that class (or subclasses).
    Higher ``priority`` runs first; ties keep subscription order. Sync and
    async handlers may be mixed freely on the same event type.
    """

    def __init__(self) -> None:
        # Flat list, not keyed by type: isinstance dispatch makes a per-type
        # index useless for inheritance fan-out, and an in-memory bus has at
        # most hundreds of subs -- O(n) scan beats MRO-aware buckets.
        self._subs: list[_Subscription] = []
        self._handles = count()

    def subscribe(
        self,
        event_type: type,
        handler: Callable | None = None,
        *,
        priority: int = 0,
    ) -> int | Callable:
        """Register ``handler`` for events of ``event_type`` (and subclasses).

        With a handler: returns an opaque handle usable with :meth:`unsubscribe`.
        Without one, acts as a decorator -- ``@bus.subscribe(SomeEvent)`` or
        ``@bus.subscribe(SomeEvent, priority=5)`` registers the decorated
        function and returns it unchanged.
        """
        if not inspect.isclass(event_type):
            raise TypeError(f"event_type must be a class, got {event_type!r}")
        if handler is None:
            # Decorator form: register at decoration time (i.e. when the def
            # below executes) and return the function unchanged so ordinary use
            # of the decorated name keeps working.
            def decorator(fn):
                self._register(event_type, fn, priority)
                return fn

            return decorator
        return self._register(event_type, handler, priority)

    def _register(self, event_type: type, handler: Callable, priority: int) -> int:
        if not callable(handler):
            raise TypeError(f"handler must be callable, got {handler!r}")
        handle = next(self._handles)
        self._subs.append(
            _Subscription(handle=handle, event_type=event_type, handler=handler, priority=priority)
        )
        return handle

    def unsubscribe(self, handle: int) -> None:
        """Remove the subscription identified by ``handle``; ValueError if unknown."""
        for i, sub in enumerate(self._subs):
            if sub.handle == handle:
                del self._subs[i]
                return
        raise ValueError(f"unknown subscription handle {handle!r}")

    async def publish(self, event: Any) -> list[HandlerResult]:
        """Deliver ``event`` to every matching subscriber; never raises handler errors.

        Returns one :class:`HandlerResult` per matched subscription, in the
        order the handlers actually ran (priority then subscription order).
        """
        matched = [s for s in self._subs if isinstance(event, s.event_type)]
        results: list[HandlerResult] = []
        # (-priority, handle): higher priority first; handles are monotonic so
        # ties resolve to subscription order deterministically.
        for sub in sorted(matched, key=lambda s: (-s.priority, s.handle)):
            try:
                # Call then check: isawaitable covers async def, callable
                # classes with async __call__, and wrappers returning a
                # coroutine -- the cases pre-inspection misses.
                result = sub.handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                # Isolation policy: per-handler failures are contained and
                # reported (result + log), never propagated. BaseException is
                # intentionally outside this net -- see module docstring.
                logger.warning(
                    "handler %s raised for event %r", sub.handle, type(event).__name__, exc_info=exc
                )
                results.append(HandlerResult(sub.handle, exc))
            else:
                results.append(HandlerResult(sub.handle))
        return results

"""Unit tests for event_bus.EventBus: subscription, priority ordering, error isolation."""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass

import pytest

from event_bus import EventBus, HandlerResult


# Event types are plain classes; the bus only uses them for isinstance checks.
@dataclass
class OrderPlaced:
    order_id: str = "o-1"


# Subclass of OrderPlaced -- exercises inheritance fan-out on publish.
class OrderShipped(OrderPlaced):
    pass


@dataclass
class PaymentFailed:
    amount: float = 0.0


class TestSubscription:
    def test_subscribe_returns_distinct_handles(self):
        bus = EventBus()
        a = bus.subscribe(OrderPlaced, lambda e: None)
        b = bus.subscribe(PaymentFailed, lambda e: None)
        assert a != b

    def test_sync_handler_receives_exact_event_instance(self):
        bus = EventBus()
        seen = []
        bus.subscribe(OrderPlaced, seen.append)
        event = OrderPlaced(order_id="o-42")
        out = asyncio.run(bus.publish(event))
        assert seen == [event]  # identity: same object, not a copy
        assert len(out) == 1 and out[0].error is None

    def test_async_handler_is_awaited(self):
        bus = EventBus()
        seen = []

        async def handler(e):
            await asyncio.sleep(0)  # prove it runs on the loop, not just called
            seen.append(e)

        bus.subscribe(OrderPlaced, handler)
        event = OrderPlaced(order_id="a-1")
        out = asyncio.run(bus.publish(event))
        assert len(seen) == 1 and seen[0] is event
        assert len(out) == 1 and out[0].error is None

    def test_callable_instance_with_async_call(self):
        bus = EventBus()
        seen = []

        class Handler:
            async def __call__(self, e):
                await asyncio.sleep(0)
                seen.append(e)

        event = OrderPlaced(order_id="c-1")
        bus.subscribe(OrderPlaced, Handler())
        out = asyncio.run(bus.publish(event))
        assert len(seen) == 1 and seen[0] is event
        assert len(out) == 1 and out[0].error is None

    def test_sync_handler_returning_coroutine_gets_awaited(self):
        bus = EventBus()
        seen = []

        async def inner(e):
            await asyncio.sleep(0)
            seen.append(e)

        # Sync wrapper returning a coroutine: the isawaitable contract means
        # the bus must await it, not discard it.
        bus.subscribe(OrderPlaced, lambda e: inner(e))
        event = OrderPlaced(order_id="w-1")
        asyncio.run(bus.publish(event))
        assert len(seen) == 1 and seen[0] is event

    def test_unmatched_type_is_not_delivered(self):
        bus = EventBus()
        calls = []
        bus.subscribe(PaymentFailed, lambda e: calls.append(e))
        out = asyncio.run(bus.publish(OrderPlaced()))
        assert calls == [] and out == []

    def test_subclass_event_reaches_base_subscription(self):
        bus = EventBus()
        base_seen, exact_seen = [], []
        bus.subscribe(OrderPlaced, lambda e: base_seen.append(1))
        bus.subscribe(OrderShipped, lambda e: exact_seen.append(1))
        asyncio.run(bus.publish(OrderShipped(order_id="s-1")))
        assert (len(base_seen), len(exact_seen)) == (1, 1)

    def test_publish_with_no_subscribers_returns_empty(self):
        out = asyncio.run(EventBus().publish(PaymentFailed()))
        assert out == []

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        calls = []
        h = bus.subscribe(OrderPlaced, lambda e: calls.append(1))
        bus.unsubscribe(h)
        asyncio.run(bus.publish(OrderPlaced()))
        assert calls == []

    def test_unsubscribe_unknown_handle_raises(self):
        with pytest.raises(ValueError):
            EventBus().unsubscribe(999)

    @pytest.mark.parametrize("bad_type", ["OrderPlaced", None, 42])
    def test_subscribe_rejects_non_class_event_type(self, bad_type):
        with pytest.raises(TypeError):
            EventBus().subscribe(bad_type, lambda e: None)

    def test_subscribe_rejects_non_callable_handler(self):
        with pytest.raises(TypeError):
            EventBus().subscribe(OrderPlaced, "not-callable")


class TestDecoratorForm:
    def test_decorated_handler_is_called(self):
        bus = EventBus()
        calls = []

        @bus.subscribe(OrderPlaced)
        def handler(e):
            calls.append(e)

        event = OrderPlaced(order_id="d-1")
        asyncio.run(bus.publish(event))
        assert len(calls) == 1 and calls[0] is event

    def test_decorator_priority_is_respected(self):
        bus = EventBus()
        order = []

        @bus.subscribe(OrderPlaced)
        def low(e):
            order.append("low")

        @bus.subscribe(OrderPlaced, priority=10)
        def high(e):
            order.append("high")

        asyncio.run(bus.publish(OrderPlaced()))
        assert order == ["high", "low"]


class TestPriorityOrdering:
    def test_higher_priority_runs_first_despite_insertion_order(self):
        bus = EventBus()
        order = []
        # Inserted in scrambled priority order; dispatch must override it.
        bus.subscribe(OrderPlaced, lambda e: order.append("zero"))  # default 0
        bus.subscribe(OrderPlaced, lambda e: order.append("ten"), priority=10)
        bus.subscribe(OrderPlaced, lambda e: order.append("neg"), priority=-5)
        asyncio.run(bus.publish(OrderPlaced()))
        assert order == ["ten", "zero", "neg"]

    def test_default_priority_is_zero(self):
        bus = EventBus()
        order = []
        bus.subscribe(OrderPlaced, lambda e: order.append("mid"))  # no priority -> 0
        bus.subscribe(OrderPlaced, lambda e: order.append("above"), priority=1)
        bus.subscribe(OrderPlaced, lambda e: order.append("below"), priority=-1)
        asyncio.run(bus.publish(OrderPlaced()))
        assert order == ["above", "mid", "below"]

    def test_same_priority_preserves_subscription_order(self):
        bus = EventBus()
        order = []
        for name in ("a", "b", "c"):
            # n=name default-arg: classic late-binding-lambda trap, avoided.
            bus.subscribe(OrderPlaced, lambda e, n=name: order.append(n), priority=5)
        asyncio.run(bus.publish(OrderPlaced()))
        assert order == ["a", "b", "c"]

    def test_mixed_sync_and_async_run_in_combined_priority_order(self):
        bus = EventBus()
        order = []

        # Suspends mid-dispatch on purpose: the sync handlers bracketing it by
        # priority prove ordering survives a suspended async handler.
        async def slow(e):
            await asyncio.sleep(0)
            order.append("async-2")

        bus.subscribe(OrderPlaced, lambda e: order.append("sync-3"), priority=3)
        bus.subscribe(OrderPlaced, slow, priority=2)
        bus.subscribe(OrderPlaced, lambda e: order.append("sync-1"), priority=1)
        asyncio.run(bus.publish(OrderPlaced()))
        assert order == ["sync-3", "async-2", "sync-1"]


class TestErrorIsolation:
    def test_failing_handler_does_not_block_others(self):
        bus = EventBus()
        order = []
        boom = RuntimeError("boom")

        bus.subscribe(OrderPlaced, lambda e: order.append("high"), priority=10)

        def bad(e):
            raise boom

        bus.subscribe(OrderPlaced, bad, priority=5)
        bus.subscribe(OrderPlaced, lambda e: order.append("low"), priority=-1)

        out = asyncio.run(bus.publish(OrderPlaced()))  # must not propagate
        assert order == ["high", "low"]
        errors = [r for r in out if r.error is not None]
        assert len(out) == 3 and len(errors) == 1
        assert errors[0].error is boom

    def test_highest_priority_failing_does_not_stop_rest(self):
        bus = EventBus()
        order = []

        def bad(e):
            raise ValueError("first")

        # Fails first in dispatch order; both lower-priority handlers still run.
        bus.subscribe(OrderPlaced, bad, priority=9)
        for i, p in enumerate([5, 1]):
            bus.subscribe(OrderPlaced, lambda e, n=i: order.append(n), priority=p)

        out = asyncio.run(bus.publish(OrderPlaced()))
        assert order == [0, 1]
        assert sum(r.error is None for r in out) == 2

    def test_async_handler_exception_is_isolated(self):
        bus = EventBus()
        after = []

        async def bad(e):
            raise KeyError("async-boom")

        bus.subscribe(OrderPlaced, bad, priority=1)
        bus.subscribe(OrderPlaced, lambda e: after.append(1), priority=0)
        out = asyncio.run(bus.publish(OrderPlaced()))
        assert len(after) == 1
        key_errors = [r for r in out if isinstance(r.error, KeyError)]
        assert len(key_errors) == 1

    def test_multiple_failures_are_all_reported(self):
        bus = EventBus()

        class E1(Exception):
            pass

        class E2(Exception):
            pass

        def bad1(e):
            raise E1()

        def bad2(e):
            raise E2()

        ok = []
        bus.subscribe(OrderPlaced, bad1, priority=3)
        bus.subscribe(OrderPlaced, lambda e: ok.append("ok"), priority=2)
        bus.subscribe(OrderPlaced, bad2, priority=1)
        out = asyncio.run(bus.publish(OrderPlaced()))
        assert ok == ["ok"]
        names = sorted(type(r.error).__name__ for r in out if r.error is not None)
        assert names == ["E1", "E2"]

    def test_base_exception_propagates_out_of_publish(self):
        bus = EventBus()

        async def bad(e):
            raise asyncio.CancelledError("cancel!")

        # Contract: only Exception is isolated. BaseException (e.g.
        # CancelledError) must escape publish() so task shutdown works.
        bus.subscribe(OrderPlaced, bad)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bus.publish(OrderPlaced()))

    def test_successful_results_carry_handle(self):
        bus = EventBus()
        h = bus.subscribe(OrderPlaced, lambda e: None)
        out = asyncio.run(bus.publish(OrderPlaced()))
        assert [r.handle for r in out] == [h]


class TestHandlerResultShape:
    def test_result_is_frozen_dataclass(self):
        r = HandlerResult(handle=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.error = ValueError("late")

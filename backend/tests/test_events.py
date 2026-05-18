from __future__ import annotations

import asyncio

import pytest

from gravelord.events import EventBus


@pytest.mark.asyncio
async def test_publish_to_subscriber():
    bus = EventBus()
    q = await bus.subscribe()
    await bus.publish("worker_dispatched", issue_identifier="o/r#1", turn=1)
    evt = await asyncio.wait_for(q.get(), timeout=1.0)
    assert evt["event"] == "worker_dispatched"
    assert evt["issue_identifier"] == "o/r#1"
    assert evt["data"] == {"turn": 1}


@pytest.mark.asyncio
async def test_recent_buffer_per_issue():
    bus = EventBus(history_per_issue=3)
    for i in range(5):
        await bus.publish("turn_completed", issue_identifier="o/r#7", turn=i)
    recent = bus.recent("o/r#7", n=10)
    # ring buffer trims to 3
    assert len(recent) == 3
    assert [e["data"]["turn"] for e in recent] == [2, 3, 4]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = await bus.subscribe()
    await bus.unsubscribe(q)
    await bus.publish("worker_dispatched")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)

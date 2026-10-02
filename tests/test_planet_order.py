"""Planet orders: a day's earlier order is reused, not placed again; waits are visible and capped."""
import asyncio

import pytest

from akk_turbidity.monitor import planet as pl
from akk_turbidity.monitor.turbidity_monitor import DayDeferred

TARGET = "projects/p/assets/imagery/planet/daily/2026-10-01"
COLLECTION = "imagery/planet/daily/2026-10-01"
NAME = "2026-09-30T14:00:00+00:00"


def order(oid, state, collection=COLLECTION, name=NAME):
    return {"id": oid, "name": name, "state": state, "created_on": "2026-10-02T13:05:00Z",
            "delivery": {"google_earth_engine": {"project": "p", "collection": collection}}}


class FakeOrders:
    """Orders API stand-in: list_orders yields newest first; get_order walks through states."""

    def __init__(self, orders=(), states=()):
        self.orders, self.states = list(orders), list(states)

    async def list_orders(self, name=None, limit=100):
        for o in self.orders:
            if o["name"] == name:
                yield o

    async def get_order(self, order_id):
        state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
        return {"id": order_id, "state": state}


@pytest.fixture
def gee(monkeypatch):
    """{collection: [image names]} for the day collections that exist."""
    assets = {}
    monkeypatch.setattr(pl.ee.data, "getInfo", lambda path: {"id": path} if path in assets else None)
    monkeypatch.setattr(pl.ee.data, "listImages", lambda path: {"images": [{"name": n} for n in assets[path]]})
    return assets


def monitor(timeout_s=2100):
    obj = pl.PlanetTurbidity.__new__(pl.PlanetTurbidity)
    obj.target, obj.date = TARGET, "2026-10-01"
    obj.order_timeout_s, obj.order_poll_s = timeout_s, 0
    return obj


def previous(orders, gee_assets):
    return asyncio.run(monitor().previous_order(FakeOrders(orders), NAME, COLLECTION))


def test_finished_order_with_images_is_reused(gee):
    gee[TARGET] = ["img1", "img2"]
    assert previous([order("new", "success"), order("old", "failed")], gee)["id"] == "new"


def test_order_still_running_is_reused(gee):
    gee[TARGET] = []
    assert previous([order("o1", "running")], gee)["id"] == "o1"


def test_no_previous_order(gee):
    assert previous([], gee) is None


def test_failed_newest_order_means_order_again(gee):
    gee[TARGET] = ["img1"]
    assert previous([order("o2", "failed"), order("o1", "success")], gee) is None


def test_finished_order_whose_images_were_deleted_means_order_again(gee):
    gee[TARGET] = []
    assert previous([order("o1", "success")], gee) is None
    del gee[TARGET]
    assert previous([order("o1", "running")], gee) is None


def test_same_name_delivered_elsewhere_is_ignored(gee):
    gee[TARGET] = ["img1"]
    assert previous([order("x", "success", collection="imagery/planet/baseline")], gee) is None


def test_wait_returns_final_order_and_reports_each_poll(capsys):
    client = FakeOrders(states=["queued", "running", "success"])
    assert asyncio.run(monitor().wait_for_order(client, "o1"))["state"] == "success"
    out = capsys.readouterr().out
    assert [line.split(":")[1].split()[0] for line in out.splitlines()] == ["queued", "running", "success"]


def test_wait_gives_up_as_deferred_not_failed():
    client = FakeOrders(states=["running"])
    with pytest.raises(DayDeferred, match="o1 still running"):
        asyncio.run(monitor(timeout_s=0).wait_for_order(client, "o1"))

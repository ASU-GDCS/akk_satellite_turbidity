"""_wait_for_export: honest error messages and no orphaned Earth Engine tasks."""
import pytest

from akk_turbidity.monitor import turbidity_monitor as tm


class FakeTask:
    def __init__(self, states):
        self.states = list(states)
        self.cancelled = False

    def status(self):
        state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
        return {"state": state, "id": "TASK1"}

    def cancel(self):
        self.cancelled = True


class M(tm.TurbidityMonitor):
    def __init__(self):
        self.name, self.date = "sentinel2", "2026-01-01"
    get_ImageCollections = diff_baseline = configure_deliverables = get_vectors = lambda self: None


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tm.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(tm.time, "time", lambda: clock["t"])
    resets = []
    monkeypatch.setattr(tm.ee, "Reset", lambda: resets.append(1))
    return resets


def test_completed_task_passes():
    M()._wait_for_export(FakeTask(["READY", "RUNNING", "COMPLETED"]), "vector")


def test_stuck_in_queue_is_cancelled_and_reported(no_waiting):
    task = FakeTask(["READY"])
    with pytest.raises(tm.ExportError, match="TASK1 ended in state READY"):
        M()._wait_for_export(task, "raster")
    assert task.cancelled and no_waiting


def test_failed_task_is_not_cancelled():
    task = FakeTask(["RUNNING", "FAILED"])
    with pytest.raises(tm.ExportError, match="state FAILED"):
        M()._wait_for_export(task, "vector")
    assert not task.cancelled

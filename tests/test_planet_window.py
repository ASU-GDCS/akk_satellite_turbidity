"""The Planet search window must not depend on the host's timezone."""
import time
from datetime import datetime, timezone

import pytest

from akk_turbidity.monitor.planet import PlanetTurbidity


@pytest.mark.parametrize("tz", ["UTC", "America/Phoenix", "Pacific/Honolulu"])
def test_window_is_the_same_in_any_host_timezone(monkeypatch, tz):
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        obj = PlanetTurbidity.__new__(PlanetTurbidity)
        obj.set_time_interval("2026-09-28")
        start = datetime.fromtimestamp(obj.begin / 1000, tz=timezone.utc).isoformat()
        stop = datetime.fromtimestamp(obj.end / 1000, tz=timezone.utc).isoformat()
        # Historical convention (kept for consistency): the window labelled D starts at
        # D-1 14:00 UTC, so it holds the late-morning pass of Hawaii day D-1.
        assert (start, stop) == ("2026-09-27T14:00:00+00:00", "2026-09-28T14:00:00+00:00")
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()

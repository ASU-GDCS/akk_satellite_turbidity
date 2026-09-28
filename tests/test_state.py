import datetime as dt
from pathlib import Path

from akk_turbidity import config, state

D = dt.date.fromisoformat


def sat(**kw):
    base = dict(name="sentinel2", label="Sentinel2", prefix="p/", baselines="b", lag_days=5,
                processed_through=D("2026-09-20"))
    base.update(kw)
    return config.Satellite(**base)


def test_pending_starts_after_processed_through_and_respects_lag():
    days = state.pending_days(sat(), set(), today=D("2026-09-30"), lookback_days=30)
    assert days == [D("2026-09-21"), D("2026-09-22"), D("2026-09-23"), D("2026-09-24"), D("2026-09-25")]


def test_pending_skips_done_and_retries_failed_gaps():
    done = {D("2026-09-21"), D("2026-09-23")}
    days = state.pending_days(sat(), done, today=D("2026-09-30"), lookback_days=30)
    assert days == [D("2026-09-22"), D("2026-09-24"), D("2026-09-25")]


def test_lookback_limits_old_retries():
    days = state.pending_days(sat(processed_through=D("2026-01-01")), set(), today=D("2026-09-30"), lookback_days=10)
    assert days[0] == D("2026-09-20") and days[-1] == D("2026-09-25")


def test_nothing_pending_when_lag_not_reached():
    assert state.pending_days(sat(), set(), today=D("2026-09-25"), lookback_days=30) == []


def test_split_covers_every_day_once():
    days = [D("2026-09-01") + dt.timedelta(days=i) for i in range(9)]
    parts = [state.split_for_node(days, i, 4) for i in range(4)]
    assert sorted(d for p in parts for d in p) == days
    assert max(map(len, parts)) - min(map(len, parts)) <= 1


def test_repo_config_loads():
    cfg = config.load(Path(__file__).parents[1] / "config" / "pipeline.toml")
    assert set(cfg.satellites) == {"landsat", "sentinel2", "planet"}
    assert cfg.satellites["planet"].local_aoi.exists()
    assert cfg.layer.exists()

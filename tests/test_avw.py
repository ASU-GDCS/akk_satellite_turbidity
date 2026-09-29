"""compute_avw sensor handling, with Earth Engine stubbed out (no server needed)."""
import types

import pytest

from akk_turbidity.monitor import utils


class FakeImage:
    def __init__(self, log):
        self.log = log

    def select(self, bands):
        self.log.append(("select", tuple(bands)))
        return self

    def divide(self, x):
        self.log.append(("divide", tuple(x) if isinstance(x, list) else x))
        return self

    def reduce(self, _):
        return self

    def expression(self, expr, env):
        self.log.append(("coeffs", tuple(env["c"])))
        return self

    def toFloat(self):
        return self


@pytest.fixture
def stub_ee(monkeypatch):
    monkeypatch.setattr(utils, "ee", types.SimpleNamespace(Reducer=types.SimpleNamespace(sum=lambda: "sum")))


def run(sensor):
    log = []
    utils.compute_avw(FakeImage(log), sensor)
    return log


def test_sentinel_2c_uses_2a_coefficients(stub_ee):
    assert run("Sentinel-2C") == run("Sentinel-2A")
    assert run("Sentinel-2C") != run("Sentinel-2B")


def test_unknown_sensor_fails_clearly(stub_ee):
    with pytest.raises(ValueError, match="Sentinel-2D"):
        run("Sentinel-2D")

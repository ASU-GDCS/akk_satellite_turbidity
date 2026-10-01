"""cleanup: a missing day collection is the normal case and stays quiet; real errors surface."""
import pytest

from akk_turbidity.monitor import planet as pl

TARGET = "projects/p/assets/imagery/planet/daily/2026-09-30"


@pytest.fixture
def gee(monkeypatch):
    """A fake asset store: {asset id: [image names]} for collections that exist."""
    assets, deleted = {}, []

    def delete(path):
        if path not in assets and not any(path in images for images in assets.values()):
            raise RuntimeError(f"Asset '{path}' does not exist")
        deleted.append(path)

    monkeypatch.setattr(pl.ee.data, "getInfo", lambda path: {"id": path} if path in assets else None)
    monkeypatch.setattr(pl.ee.data, "listImages",
                        lambda path: {"images": [{"name": n} for n in assets[path]]})
    monkeypatch.setattr(pl.ee.data, "deleteAsset", delete)
    return assets, deleted


def monitor():
    obj = pl.PlanetTurbidity.__new__(pl.PlanetTurbidity)
    obj.target = TARGET
    obj.init_ee = lambda: None
    return obj


def test_missing_collection_deletes_nothing_and_prints_nothing(gee, capsys):
    _, deleted = gee
    monitor().cleanup()
    assert deleted == []
    assert capsys.readouterr().out == ""


def test_existing_collection_is_emptied_then_deleted(gee):
    assets, deleted = gee
    assets[TARGET] = [f"{TARGET}/img1", f"{TARGET}/img2"]
    monitor().cleanup()
    assert deleted == [f"{TARGET}/img1", f"{TARGET}/img2", TARGET]


def test_real_delete_failure_is_raised(gee, monkeypatch):
    assets, _ = gee
    assets[TARGET] = []

    def denied(path):
        raise PermissionError("caller does not have access")

    monkeypatch.setattr(pl.ee.data, "deleteAsset", denied)
    with pytest.raises(PermissionError):
        monitor().cleanup()

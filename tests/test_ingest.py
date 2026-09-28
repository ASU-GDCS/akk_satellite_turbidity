import gzip
import json

import pytest

from akk_turbidity import config
from akk_turbidity.ingest import archive, cli, collect, layer, prepare

from conftest import FakeClient, gee_export, layer_feature, md5, square

BUCKET = "hawaii-bucket"


def setup_repo(repo, existing):
    layer.write_layer(repo / "data" / "turbidity_outlines.geojson", existing, "turbidity_outlines")
    return config.load(repo / "config" / "pipeline.toml")


def put(client, name, data):
    client.store.put(name, data)


def test_plan_update_dedupes_unions_and_filters():
    existing = [layer_feature("Landsat", "2026-01-01")]
    # overlapping squares on the same date/satellite are unioned into one polygon
    big = square(-156.0, 19.6, 0.03)       # ~10 ha each
    big2 = square(-155.99, 19.6, 0.03)
    tiny_a = square(-155.5, 19.6, 0.001)   # union < 5 ha -> dropped
    tiny_b = square(-155.4, 19.6, 0.001)
    mk = lambda ring, sat, date: {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                                  "properties": {"Satelite": sat, "ID": 1, "Date": date, "PixelCount": 100, "Area_ha": 10.0}}
    v = lambda sat, label, date: collect.SourceVector(name=f"x/{date}", generation=1, md5_hash=None, size=1,
                                                      satellite=sat, label=label, date=date)
    dup = v("landsat", "Landsat", "2026-01-01")
    s2 = v("sentinel2", "Sentinel2", "2026-01-05")
    pl = v("planet", "PlanetScope", "2026-01-06")
    empty = v("landsat", "Landsat", "2026-01-07")
    cands = [
        (dup, "new", [mk(big, "Landsat", "2026-01-01")]),
        (s2, "new", [mk(big, "Sentinel2", "2026-01-05"), mk(big2, "Sentinel2", "2026-01-05"),
                     mk(tiny_a, "Sentinel2", "2026-01-05"), mk(tiny_b, "Sentinel2", "2026-01-05")]),
        (pl, "new", [mk(tiny_a, "PlanetScope", "2026-01-06")]),   # single record: kept as-is (old behaviour)
        (empty, "empty", []),
    ]
    merged, new = prepare.plan_update(existing, cands, log=lambda *a: None)
    assert dup.status == "duplicate" and empty.status == "empty"
    s2_new = [f for f in new if f["properties"]["Satelite"] == "Sentinel2"]
    assert len(s2_new) == 1 and s2_new[0]["properties"]["Area_ha"] > 5
    assert s2.n_features == 1 and pl.n_features == 1
    assert [f["properties"]["Date"] for f in merged] == ["2026-01-01", "2026-01-05", "2026-01-06"]
    assert merged[0] is existing[0]


def test_layer_round_trip_is_byte_stable(tmp_path):
    feats = [layer_feature("Landsat", "2026-01-01"), layer_feature("PlanetScope", "2026-01-02", fid=3)]
    p = tmp_path / "l.geojson"
    layer.write_layer(p, feats, "t")
    text = p.read_text()
    assert layer.dumps_layer(layer.read_layer(p), "t") == text
    assert text.count("\n") == 5 + len(feats) + 2   # header, one line per feature, footer


def test_prepare_apply_archive_end_to_end(repo, monkeypatch):
    cfg = setup_repo(repo, [layer_feature("Landsat", "2026-01-28")])
    client = FakeClient()
    old = gee_export([(square(-155.84, 19.97, 0.002), 22, 19830.4)])
    new = gee_export([(square(-155.9, 19.8, 0.01), 500, 90000.0)])
    put(client, "TurbidityTest/landsat/2026/vector/2026-01-28.geojson", old)      # already published
    put(client, "TurbidityTest/sentinel2/2026/vector/2026-02-01.geojson", new)    # late arrival -> backfill
    put(client, "TurbidityTest/sentinel2/2026/vector/2026-02-02.geojson",
        b'{"type":"FeatureCollection","features":[]}')
    put(client, "TurbidityTest/sentinel2/2026/raster/2026-02-01_0.tif", b"tif")   # never touched
    put(client, "TurbidityTest/archive/landsat/2025/vector/2025-12-01.geojson.gz", b"x")  # not re-listed

    out = repo / "build" / "ingest"
    manifest = prepare.prepare(cfg, client, out, log=lambda *a: None)
    status = {v["name"].rsplit("/", 1)[1]: v["status"] for v in manifest["vectors"]}
    assert status == {"2026-01-28.geojson": "duplicate", "2026-02-01.geojson": "new", "2026-02-02.geojson": "empty"}
    assert manifest["new_features"] == 1
    summary = (out / "review" / "summary.md").read_text()
    assert "backfill" not in summary          # 2026-02-01 is newer than the layer's latest date
    assert (out / "review" / "map.html").read_text().count("L.geoJSON(NEW") == 1

    # apply: refuses if the layer changed after prepare
    monkeypatch.chdir(repo)
    base = cfg.layer.read_text()
    cfg.layer.write_text(base + " ")
    assert cli.main(["--config", str(repo / "config/pipeline.toml"), "apply", "--out", str(out)]) == 1
    cfg.layer.write_text(base)
    msg = out / "msg.txt"
    assert cli.main(["--config", str(repo / "config/pipeline.toml"), "apply", "--out", str(out),
                     "--message-file", str(msg)]) == 0
    published = layer.read_layer(cfg.layer)
    assert [f["properties"]["Date"] for f in published] == ["2026-01-28", "2026-02-01"]
    assert msg.read_text().startswith("Add 1 turbidity outlines (2026-02-01 to 2026-02-01)")

    # archive: every manifest file gzipped + verified, originals removed, raster untouched
    results, failures = archive.archive_manifest(cfg, client, manifest, log=lambda *a: None)
    assert failures == [] and results == {"archived": 3}
    names = set(client.store.objects)
    assert "TurbidityTest/sentinel2/2026/raster/2026-02-01_0.tif" in names
    assert not any("/vector/" in n and not n.startswith("TurbidityTest/archive/") for n in names)
    arc = client.store.objects["TurbidityTest/archive/sentinel2/2026/vector/2026-02-01.geojson.gz"]
    assert gzip.decompress(arc["data"]) == new
    assert arc["metadata"]["source_md5"] == md5(new) and arc["content_type"] == "application/gzip"

    # re-running is harmless
    results, failures = archive.archive_manifest(cfg, client, manifest, log=lambda *a: None)
    assert failures == [] and results == {"already_archived": 3}

    # and the next prepare sees nothing new
    manifest2 = prepare.prepare(cfg, client, repo / "build" / "ingest2", log=lambda *a: None)
    assert manifest2["vectors"] == [] and manifest2["new_features"] == 0


def _one(cfg, client, name, data):
    client.store.put(name, data)
    blob = client.bucket(BUCKET).blob(name)
    return collect.SourceVector(name=name, generation=blob.generation, md5_hash=blob.md5_hash, size=len(data),
                                satellite="landsat", label="Landsat", date="2026-03-01", status="new")


def test_archive_refuses_changed_content(repo):
    cfg = setup_repo(repo, [])
    client = FakeClient()
    vec = _one(cfg, client, "TurbidityTest/landsat/2026/vector/2026-03-01.geojson", b"reviewed")
    vec.md5_hash = md5(b"something else")
    with pytest.raises(RuntimeError, match="changed since review"):
        archive.archive_one(cfg, client.bucket(BUCKET), vec, log=lambda *a: None)
    assert "TurbidityTest/landsat/2026/vector/2026-03-01.geojson" in client.store.objects


def test_archive_skips_when_reviewed_generation_was_replaced(repo):
    cfg = setup_repo(repo, [])
    client = FakeClient()
    name = "TurbidityTest/landsat/2026/vector/2026-03-01.geojson"
    vec = _one(cfg, client, name, b"reviewed")
    # the reviewed generation is replaced by a re-export before archiving runs
    client.store.put(name, b"re-exported")
    # the reviewed generation is gone, so nothing verifiable to archive
    with pytest.raises(RuntimeError, match="no verified archive copy"):
        archive.archive_one(cfg, client.bucket(BUCKET), vec, log=lambda *a: None)
    assert client.store.objects[name]["data"] == b"re-exported"


def test_archive_does_not_overwrite_different_archive(repo):
    cfg = setup_repo(repo, [])
    client = FakeClient()
    name = "TurbidityTest/landsat/2026/vector/2026-03-01.geojson"
    client.store.put("TurbidityTest/archive/landsat/2026/vector/2026-03-01.geojson.gz", gzip.compress(b"older run"))
    vec = _one(cfg, client, name, b"reviewed")
    assert archive.archive_one(cfg, client.bucket(BUCKET), vec, log=lambda *a: None) == "archived"
    assert gzip.decompress(client.store.objects[
        "TurbidityTest/archive/landsat/2026/vector/2026-03-01.geojson.gz"]["data"]) == b"older run"
    alt = f"TurbidityTest/archive/landsat/2026/vector/2026-03-01.geojson.gen{vec.generation}.gz"
    assert gzip.decompress(client.store.objects[alt]["data"]) == b"reviewed"
    assert name not in client.store.objects


def test_archive_keeps_original_if_verification_fails(repo, monkeypatch):
    cfg = setup_repo(repo, [])
    client = FakeClient()
    name = "TurbidityTest/landsat/2026/vector/2026-03-01.geojson"
    vec = _one(cfg, client, name, b"reviewed")
    monkeypatch.setattr(archive, "_verified_copy", lambda *a: False)
    with pytest.raises(RuntimeError, match="failed verification"):
        archive.archive_one(cfg, client.bucket(BUCKET), vec, log=lambda *a: None)
    assert name in client.store.objects


def test_exclusions(tmp_path):
    ex = tmp_path / "exclusions.txt"
    ex.write_text("# comment\n2026-02-01 Sentinel2  # false positive\n\n")
    excluded = prepare.read_exclusions(ex)
    assert excluded == {("Sentinel2", "2026-02-01")}
    vec = collect.SourceVector(name="x", generation=1, md5_hash=None, size=1, satellite="sentinel2",
                               label="Sentinel2", date="2026-02-01")
    feat = layer_feature("Sentinel2", "2026-02-01")
    merged, new = prepare.plan_update([], [(vec, "new", [feat])], excluded=excluded, log=lambda *a: None)
    assert vec.status == "excluded" and new == [] and merged == []
    ex.write_text("2026-02-01\n")
    with pytest.raises(ValueError):
        prepare.read_exclusions(ex)

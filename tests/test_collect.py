from akk_turbidity.ingest import collect

from conftest import gee_export, square


def test_parse_vector_name():
    p = "TurbidityTest/landsat/"
    assert collect.parse_vector_name(p + "2026/vector/2026-01-28.geojson", p) == "2026-01-28"
    assert collect.parse_vector_name(p + "2026/raster/2026-01-28_0.tif", p) is None
    assert collect.parse_vector_name("TurbidityTest/planet/2026/vector/2026-01-28.geojson", p) is None
    assert collect.parse_vector_name(p + "2026/vector/2026-02-30.geojson", p) is None
    assert collect.parse_vector_name(p + "2026/vector/notes.txt", p) is None


def test_gdal_int_matches_old_int_field_coercion():
    assert collect.gdal_int("+1236+237263") == 1236
    assert collect.gdal_int("-5+3") == -5
    assert collect.gdal_int("abc") == 0
    assert collect.gdal_int(7) == 7
    assert collect.gdal_int(None) is None


def test_read_features_converts_gee_export(tmp_path):
    f = tmp_path / "2026-01-28.geojson"
    f.write_bytes(gee_export([(square(-155.84, 19.97, 0.002), 22, 19830.4)]))
    status, feats = collect.read_features(f, "Landsat", "2026-01-28")
    assert status == "new"
    p = feats[0]["properties"]
    assert p == {"Satelite": "Landsat", "ID": 1236, "Date": "2026-01-28", "PixelCount": 22,
                 "Area_ha": 19830.4 / 10000}
    assert feats[0]["geometry"]["type"] == "Polygon"


def test_read_features_empty(tmp_path):
    f = tmp_path / "e.geojson"
    f.write_text('{"type":"FeatureCollection","features":[]}')
    assert collect.read_features(f, "Landsat", "2026-01-01") == ("empty", [])

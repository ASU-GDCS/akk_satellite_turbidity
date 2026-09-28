"""Read/write the published outline layer.

The layer is written with the ``json`` module (not GDAL) so that existing
features round-trip byte-for-byte and each weekly update is an append-only
git diff: one feature per line, fixed property order, CRS84 coordinates.
"""
import hashlib
import json
from pathlib import Path

# Property order of the published layer (matches the historical AGOL layer).
PROPS = ("Satelite", "ID", "Date", "PixelCount", "Area_ha")
CRS84 = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}


def normalize_feature(feature):
    props = feature["properties"]
    missing = [k for k in PROPS if k not in props]
    if missing:
        raise ValueError(f"feature missing properties {missing}: {props}")
    return {
        "type": "Feature",
        "properties": {
            "Satelite": str(props["Satelite"]),
            "ID": None if props["ID"] is None else int(props["ID"]),
            "Date": str(props["Date"]),
            "PixelCount": None if props["PixelCount"] is None else int(props["PixelCount"]),
            "Area_ha": None if props["Area_ha"] is None else float(props["Area_ha"]),
        },
        "geometry": feature["geometry"],
    }


def sort_key(feature):
    p = feature["properties"]
    return (p["Date"], p["Satelite"])


def read_layer(path):
    with open(path) as f:
        return json.load(f)["features"]


def dumps_layer(features, name):
    head = ("{\n"
            '"type":"FeatureCollection",\n'
            f'"name":{json.dumps(name)},\n'
            f'"crs":{json.dumps(CRS84, separators=(",", ":"))},\n'
            '"features":[\n')
    body = ",\n".join(json.dumps(normalize_feature(f), separators=(",", ":"), allow_nan=False)
                      for f in features)
    return head + body + ("\n" if body else "") + "]\n}\n"


def write_layer(path, features, name):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dumps_layer(features, name))
    tmp.replace(path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

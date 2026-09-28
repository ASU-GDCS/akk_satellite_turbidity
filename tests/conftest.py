"""In-memory stand-ins for google-cloud-storage, enough for collect/prepare/archive."""
import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

REPO = Path(__file__).resolve().parents[1]


def md5(data):
    return base64.b64encode(hashlib.md5(data).digest()).decode()


class FakeStore:
    def __init__(self):
        self.objects = {}   # name -> dict(generation, data, metadata, content_type)
        self._gen = 1000

    def put(self, name, data, metadata=None, content_type=None):
        self._gen += 1
        self.objects[name] = dict(generation=self._gen, data=data, metadata=metadata or {},
                                  content_type=content_type)
        return self._gen


class FakeBlob:
    def __init__(self, store, bucket, name, generation=None):
        self._store, self.bucket, self.name, self._want_gen = store, bucket, name, generation
        self.metadata = None
        obj = store.objects.get(name)
        self.generation = obj["generation"] if obj else None
        self.md5_hash = md5(obj["data"]) if obj else None
        self.size = len(obj["data"]) if obj else None

    def _obj(self):
        obj = self._store.objects.get(self.name)
        if obj is None or (self._want_gen is not None and obj["generation"] != self._want_gen):
            raise NotFound(f"{self.name}#{self._want_gen}")
        return obj

    def download_as_bytes(self):
        return self._obj()["data"]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if isinstance(data, str):
            data = data.encode()
        exists = self.name in self._store.objects
        if if_generation_match == 0 and exists:
            raise PreconditionFailed(self.name)
        self._store.put(self.name, data, dict(self.metadata or {}), content_type)

    def delete(self, if_generation_match=None):
        obj = self._store.objects.get(self.name)
        if obj is None:
            raise NotFound(self.name)
        if if_generation_match is not None and obj["generation"] != if_generation_match:
            raise PreconditionFailed(self.name)
        del self._store.objects[self.name]


class FakeBucket:
    def __init__(self, store, name):
        self._store, self.name = store, name

    def blob(self, name, generation=None):
        return FakeBlob(self._store, self, name, generation)

    def get_blob(self, name):
        return FakeBlob(self._store, self, name) if name in self._store.objects else None


class _Listing(list):
    prefixes = set()


class FakeClient:
    def __init__(self, store=None):
        self.store = store or FakeStore()

    def bucket(self, name):
        return FakeBucket(self.store, name)

    def list_blobs(self, bucket_name, prefix="", delimiter=None, max_results=None):
        bucket = self.bucket(bucket_name)
        out = _Listing()
        out.prefixes = set()
        for name in sorted(self.store.objects):
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            if delimiter and delimiter in rest:
                out.prefixes.add(prefix + rest.split(delimiter)[0] + delimiter)
                continue
            out.append(bucket.blob(name))
        return out


def gee_export(polygons):
    """GeoJSON the way Earth Engine's table export writes it."""
    feats = []
    for i, (ring, count, size) in enumerate(polygons):
        feats.append({"type": "Feature",
                      "geometry": {"geodesic": False, "type": "Polygon", "coordinates": [ring]},
                      "id": f"+{1236 + i}+237263",
                      "properties": {"count": count, "size": size, "zone": 1}})
    return json.dumps({"type": "FeatureCollection", "features": feats}).encode()


def square(lon, lat, d):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def layer_feature(sat, date, lon=-156.0, lat=19.6, d=0.01, fid=1):
    return {"type": "Feature",
            "properties": {"Satelite": sat, "ID": fid, "Date": date, "PixelCount": 100, "Area_ha": 12.5},
            "geometry": {"type": "Polygon", "coordinates": [square(lon, lat, d)]}}


@pytest.fixture
def repo(tmp_path):
    """A throwaway copy of the repo's config with a small layer."""
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "pipeline.toml", tmp_path / "config" / "pipeline.toml")
    shutil.copy(REPO / "config" / "akoakoa_turbidity_roi_simple.geojson", tmp_path / "config")
    return tmp_path

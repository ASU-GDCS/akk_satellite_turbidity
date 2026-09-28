"""List and read the per-day vector exports that GEE writes to GCS.

Every ``<prefix><YYYY>/vector/<YYYY-MM-DD>*.geojson`` still in place is a
candidate: ingested files are moved to the archive prefix afterwards, so
"not yet archived" replaces the old date-range bookkeeping (which silently
skipped Landsat/Sentinel outlines exported after an ingest but dated before it).
"""
import base64
import datetime
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

VECTOR_RE = re.compile(r"^(?P<year>\d{4})/vector/(?P<date>\d{4}-\d{2}-\d{2})[^/]*\.geojson$")


@dataclass
class SourceVector:
    name: str
    generation: int
    md5_hash: str | None
    size: int
    satellite: str      # config key: landsat | sentinel2 | planet
    label: str          # value of the "Satelite" property
    date: str           # YYYY-MM-DD
    status: str = "pending"
    n_features: int = 0
    note: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def parse_vector_name(name, prefix):
    """Return the date string if ``name`` is a vector export under ``prefix``."""
    if not name.startswith(prefix):
        return None
    m = VECTOR_RE.match(name[len(prefix):])
    if not m:
        return None
    try:
        datetime.date.fromisoformat(m["date"])
    except ValueError:
        return None
    return m["date"]


def list_vectors(client, cfg):
    vectors = []
    for sat in cfg.satellites.values():
        # year "folders" first, so the (large) raster listings are never paged through
        it = client.list_blobs(cfg.bucket, prefix=sat.prefix, delimiter="/")
        for _ in it:
            pass
        years = sorted(p for p in it.prefixes if re.fullmatch(re.escape(sat.prefix) + r"\d{4}/", p))
        for year_prefix in years:
            for blob in client.list_blobs(cfg.bucket, prefix=f"{year_prefix}vector/"):
                date = parse_vector_name(blob.name, sat.prefix)
                if date is None:
                    continue
                vectors.append(SourceVector(
                    name=blob.name, generation=int(blob.generation), md5_hash=blob.md5_hash,
                    size=int(blob.size or 0), satellite=sat.name, label=sat.label, date=date))
    vectors.sort(key=lambda v: (v.date, v.label, v.name))
    return vectors


def md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def download(client, bucket_name, vector, dest_dir):
    """Download exactly the listed generation and check its MD5."""
    dest = Path(dest_dir) / vector.satellite / Path(vector.name).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = client.bucket(bucket_name).blob(vector.name, generation=vector.generation)
    data = blob.download_as_bytes()
    if vector.md5_hash and md5_b64(data) != vector.md5_hash:
        raise IOError(f"MD5 mismatch downloading {vector.name}")
    dest.write_bytes(data)
    return dest


_LEADING_INT = re.compile(r"\s*[+-]?\d+")


def gdal_int(value):
    """Integer the way GDAL's OFTInteger field coercion (atoi) produced it.

    GEE writes feature ids like "+1236+237263"; the old pipeline stored them in
    an int "ID" field, which kept the leading integer (1236). Reproduce that so
    IDs stay consistent with the historical layer.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = _LEADING_INT.match(str(value))
    return int(m.group()) if m else 0


def read_features(path, label, date):
    """Read one export and convert it to the published schema.

    Returns (status, features); status is "new", "empty" or "schema_mismatch".
    Mirrors collect_new_outlines.py: files with no features, or whose layer
    geometry type is not Polygon, are skipped.
    """
    import fiona
    import shapely.geometry as geom

    with fiona.open(path) as inref:
        scm = inref.schema
        records = [*inref]
    if len(records) < 1:
        return "empty", []
    if scm["geometry"] != "Polygon":
        return "schema_mismatch", []

    features = []
    for f in records:
        props = f["properties"]
        features.append({
            "type": "Feature",
            "properties": {
                "Satelite": label,
                "ID": gdal_int(props["id"]),
                "Date": str(date),
                "PixelCount": gdal_int(props["count"]),
                "Area_ha": props["size"] / 10000,
            },
            "geometry": geom.mapping(geom.shape(f["geometry"])),
        })
    return "new", features

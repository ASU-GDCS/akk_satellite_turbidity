"""Build the candidate layer update and the manifest of source files.

Outputs (in ``out_dir``):
  turbidity_outlines.geojson  full candidate layer (existing + new, sorted)
  manifest.json               every source vector considered, its status, and
                              the SHA-256 of the layer this update is based on
  review/                     summary.md, new_outlines.geojson, map.html
"""
import datetime
import json
import os
from pathlib import Path

from . import collect, layer, review
from .combine import combine_by_date


def read_exclusions(path):
    """{(Satelite, Date)} pairs listed in config/exclusions.txt."""
    out = set()
    path = Path(path)
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"{path}:{n}: expected '<YYYY-MM-DD> <Satelite>', got {line!r}")
        date, sat = parts
        datetime.date.fromisoformat(date)
        out.add((sat, date))
    return out


def plan_update(existing, candidates, excluded=frozenset(), log=print):
    """Pure core of the ingest.

    existing    features already in the published layer
    candidates  list of (SourceVector, status, features) for the files read

    excluded    {(Satelite, Date)} never to publish (config/exclusions.txt)

    Returns (merged_features, new_features). A (Satelite, Date) pair that is
    already in the layer is never added again, so re-running is harmless and
    the first run after the migration simply archives what was ingested by
    the old process.
    """
    have = {(f["properties"]["Satelite"], f["properties"]["Date"]) for f in existing}
    to_combine = []
    for vec, status, feats in candidates:
        if status == "new":
            if (vec.label, vec.date) in have:
                vec.status = "duplicate"
                continue
            if (vec.label, vec.date) in excluded:
                vec.status = "excluded"
                continue
            to_combine.extend(feats)
        vec.status = status

    new = [layer.normalize_feature(f) for f in combine_by_date(to_combine, log=log)]

    counts = {}
    for f in new:
        key = (f["properties"]["Satelite"], f["properties"]["Date"])
        counts[key] = counts.get(key, 0) + 1
    for vec, _, _ in candidates:
        if vec.status == "new":
            vec.n_features = counts.get((vec.label, vec.date), 0)
            if vec.n_features == 0:
                vec.note = "all outlines removed by size filters"

    merged = sorted(list(existing) + new, key=layer.sort_key)
    return merged, new


def prepare(cfg, client, out_dir, log=print):
    out_dir = Path(out_dir)
    dl_dir = out_dir / "downloads"
    review_dir = out_dir / "review"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_sha = layer.sha256_file(cfg.layer)
    existing = layer.read_layer(cfg.layer)
    have = {(f["properties"]["Satelite"], f["properties"]["Date"]) for f in existing}
    last_date = max((f["properties"]["Date"] for f in existing), default=None)
    log(f"Existing layer: {len(existing)} features, latest date {last_date}")

    vectors = collect.list_vectors(client, cfg)
    log(f"Found {len(vectors)} un-archived vector exports in gs://{cfg.bucket}")

    excluded = read_exclusions(cfg.root / "config" / "exclusions.txt")
    candidates = []
    listed_only = []
    for vec in vectors:
        if (vec.label, vec.date) in have or (vec.label, vec.date) in excluded:
            # already published; no need to download, just archive later
            candidates.append((vec, "new", []))
            continue
        try:
            path = collect.download(client, cfg.bucket, vec, dl_dir)
            status, feats = collect.read_features(path, vec.label, vec.date)
            log(f"{vec.name}: {status}, {len(feats)} feature(s)")
            candidates.append((vec, status, feats))
        except Exception as exc:
            # leave it in place (not in the manifest) so the next run retries it
            vec.status, vec.note = "error", str(exc)[:300]
            listed_only.append(vec)
            log(f"{vec.name}: download/read error, will retry next run: {exc}")

    merged, new = plan_update(existing, candidates, excluded=excluded, log=log)
    in_manifest = [vec for vec, _, _ in candidates]

    layer.write_layer(out_dir / "turbidity_outlines.geojson", merged, cfg.layer_name)
    manifest = {
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "build_url": os.environ.get("CIRCLE_BUILD_URL"),
        "layer": str(cfg.layer.relative_to(cfg.root)),
        "base_sha256": base_sha,
        "base_features": len(existing),
        "base_latest_date": last_date,
        "new_features": len(new),
        "vectors": [v.to_dict() for v in in_manifest],
        "errors": [v.to_dict() for v in listed_only],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))

    review.write_review(cfg, review_dir, manifest, existing, new)
    log(f"Candidate layer: {len(merged)} features ({len(new)} new); "
        f"{len(in_manifest)} source file(s) to archive after publishing")
    return manifest

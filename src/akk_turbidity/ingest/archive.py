"""Move ingested GEE vector exports to a gzipped archive.

For every source file in the manifest:

1. download exactly the generation that was reviewed, check its MD5;
2. upload ``<archive_prefix>/<satellite>/<YYYY>/vector/<name>.gz`` with
   ``if_generation_match=0`` (never overwrites an existing archive object);
3. read the archive copy back, gunzip it and compare MD5 with the original;
4. only then delete the original, and only if it is still that generation.

Re-running is safe: files already archived and removed are recognised and
skipped. Nothing is deleted unless its verified archive copy exists.
"""
import gzip
import os
from pathlib import PurePosixPath

from google.api_core.exceptions import NotFound, PreconditionFailed

from .collect import SourceVector, md5_b64


def archive_name(cfg, vec, suffix=""):
    fname = PurePosixPath(vec.name).name
    return f"{cfg.archive_prefix}/{vec.satellite}/{vec.date[:4]}/vector/{fname}{suffix}.gz"


def _verified_copy(bucket, name, source_md5):
    """True if ``name`` exists and gunzips to content with ``source_md5``."""
    blob = bucket.get_blob(name)
    if blob is None:
        return False
    return md5_b64(gzip.decompress(blob.download_as_bytes())) == source_md5


def archive_one(cfg, bucket, vec, dry_run=False, log=print):
    src = bucket.blob(vec.name, generation=vec.generation)
    dest_name = archive_name(cfg, vec)
    try:
        data = src.download_as_bytes()
    except NotFound:
        # Already moved by an earlier (partly failed) run?
        for name in (dest_name, archive_name(cfg, vec, f".gen{vec.generation}")):
            if vec.md5_hash and _verified_copy(bucket, name, vec.md5_hash):
                log(f"{vec.name}: already archived as {name}")
                return "already_archived"
        raise RuntimeError(f"{vec.name} (generation {vec.generation}) is gone and no verified archive copy exists")

    md5 = md5_b64(data)
    if vec.md5_hash and md5 != vec.md5_hash:
        raise RuntimeError(f"{vec.name}: content changed since review (MD5 mismatch); not archiving")

    if dry_run:
        log(f"[dry-run] {vec.name} -> {dest_name}, then delete original")
        return "dry_run"

    payload = gzip.compress(data, mtime=0)
    for name in (dest_name, archive_name(cfg, vec, f".gen{vec.generation}")):
        dest = bucket.blob(name)
        dest.metadata = {
            "source_name": vec.name,
            "source_generation": str(vec.generation),
            "source_md5": md5,
            "satellite": vec.satellite,
            "date": vec.date,
            "ingest_status": vec.status,
            "archived_by": os.environ.get("CIRCLE_BUILD_URL", "manual"),
        }
        try:
            dest.upload_from_string(payload, content_type="application/gzip", if_generation_match=0)
            break
        except PreconditionFailed:
            if _verified_copy(bucket, name, md5):
                break  # identical copy already there (re-run)
            log(f"{name} exists with different content; trying a generation-suffixed name")
    else:
        raise RuntimeError(f"{vec.name}: could not find a free archive name")

    if not _verified_copy(bucket, name, md5):
        raise RuntimeError(f"{vec.name}: archive copy {name} failed verification; original kept")

    try:
        bucket.blob(vec.name).delete(if_generation_match=vec.generation)
    except PreconditionFailed:
        log(f"{vec.name}: original was replaced by a newer export after review; kept it (archive holds the reviewed version)")
        return "archived_kept_newer"
    except NotFound:
        pass
    log(f"{vec.name} -> gs://{bucket.name}/{name}")
    return "archived"


def archive_manifest(cfg, client, manifest, dry_run=False, log=print):
    bucket = client.bucket(cfg.bucket)
    results = {}
    failures = []
    for d in manifest["vectors"]:
        vec = SourceVector.from_dict(d)
        try:
            outcome = archive_one(cfg, bucket, vec, dry_run=dry_run, log=log)
        except Exception as exc:
            outcome = "failed"
            failures.append((vec.name, exc))
            log(f"{vec.name}: FAILED: {exc}")
        results[outcome] = results.get(outcome, 0) + 1
    log("Archive summary: " + ", ".join(f"{n} {k}" for k, n in sorted(results.items())))
    return results, failures

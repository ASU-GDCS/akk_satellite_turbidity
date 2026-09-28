"""Per-day processing markers kept in GCS.

A marker ``<state_prefix>/<satellite>/<YYYY-MM-DD>.json`` means that day was
fully processed (including days with no imagery). Days without a marker are
retried by the next run, so a failed or timed-out day is never skipped
silently. Delete a marker to force a day to be reprocessed.
"""
import datetime
import json
import os


def marker_name(state_prefix, satellite, day):
    return f"{state_prefix}/{satellite}/{day.isoformat()}.json"


def done_days(client, bucket_name, state_prefix, satellite):
    days = set()
    for blob in client.list_blobs(bucket_name, prefix=f"{state_prefix}/{satellite}/"):
        stem = blob.name.rsplit("/", 1)[-1].removesuffix(".json")
        try:
            days.add(datetime.date.fromisoformat(stem))
        except ValueError:
            continue
    return days


def mark_done(client, bucket_name, state_prefix, satellite, day, n_images, seconds):
    record = {
        "satellite": satellite,
        "date": day.isoformat(),
        "n_images": n_images,
        "seconds": round(seconds, 1),
        "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "build_url": os.environ.get("CIRCLE_BUILD_URL"),
    }
    blob = client.bucket(bucket_name).blob(marker_name(state_prefix, satellite, day))
    blob.upload_from_string(json.dumps(record, indent=1), content_type="application/json")
    return record


def pending_days(sat, done, today, lookback_days):
    """Days that still need processing for ``sat``, oldest first."""
    start = max(sat.processed_through + datetime.timedelta(days=1),
                today - datetime.timedelta(days=lookback_days))
    end = today - datetime.timedelta(days=sat.lag_days)
    days = []
    day = start
    while day <= end:
        if day not in done:
            days.append(day)
        day += datetime.timedelta(days=1)
    return days


def split_for_node(days, node_index, node_total):
    """Round-robin share of ``days`` for one CircleCI parallel node."""
    return days[node_index::node_total]

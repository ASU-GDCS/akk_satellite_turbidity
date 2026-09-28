"""Run the Earth Engine turbidity monitor for one satellite.

Replaces the old ``monitor_turbidity.py`` + ``cronjob.sh`` + systemd timer.

Which days get processed:
  every day in [max(processed_through + 1, today - lookback_days), today - lag_days]
  that has no state marker in GCS (see ``state.py``). Each day is independent,
  so the pending days are split round-robin across CircleCI parallel nodes
  (CIRCLE_NODE_INDEX / CIRCLE_NODE_TOTAL). A node stops starting new days once
  ``time_budget_minutes`` have elapsed; unfinished days are picked up next run.

Exit status is non-zero if any day failed, so CircleCI reports the failure.
"""
import argparse
import datetime
import json
import os
import sys
import time
import traceback
from pathlib import Path

from . import auth, state
from . import config as cfgmod


def make_processor(sat, day, cfg, credentials):
    date_str = day.isoformat()
    common = dict(credentials=credentials, project=cfg.gee_project, date=date_str,
                  aoi_loc=cfg.aoi, bucket=cfg.bucket, prefix=sat.prefix)
    if sat.name == "landsat":
        from .monitor.landsat import LandsatTurbidity
        return LandsatTurbidity(landsat_baselines_loc=sat.baselines, **common)
    if sat.name == "sentinel2":
        from .monitor.sentinel2 import SentinelTurbidity
        return SentinelTurbidity(sentinel2_baselines_loc=sat.baselines, **common)
    if sat.name == "planet":
        from .monitor.planet import PlanetTurbidity
        return PlanetTurbidity(
            assets_root=cfg.assets_root,
            local_aoi_loc=str(sat.local_aoi),
            planet_baselines_loc=sat.baselines,
            planet_daily_loc=sat.daily_collection,
            planet_api_key=cfgmod.require_env("PLANET_API_KEY"),
            dep_loc=cfg.depth,
            **common,
        )
    raise ValueError(f"Unknown satellite {sat.name}")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--satellite", required=True, help="landsat | sentinel2 | planet")
    parser.add_argument("--config", default=cfgmod.DEFAULT_CONFIG)
    parser.add_argument("--today", type=datetime.date.fromisoformat, default=None,
                        help="override today's UTC date (testing)")
    parser.add_argument("--days", nargs="*", type=datetime.date.fromisoformat, default=None,
                        help="process exactly these days instead of the pending list (still skips days already marked done unless --force)")
    parser.add_argument("--force", action="store_true", help="with --days: reprocess even if marked done")
    parser.add_argument("--node-index", type=int, default=int(os.environ.get("CIRCLE_NODE_INDEX", 0)))
    parser.add_argument("--node-total", type=int, default=int(os.environ.get("CIRCLE_NODE_TOTAL", 1)))
    parser.add_argument("--dry-run", action="store_true", help="list the days this node would process and exit")
    parser.add_argument("--anonymous", action="store_true",
                        help="with --dry-run: read state markers anonymously (public bucket) instead of with credentials")
    parser.add_argument("--summary", default=None, help="write a JSON summary of this node's results here")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    t_start = time.monotonic()
    cfg = cfgmod.load(args.config)
    if args.satellite not in cfg.satellites:
        raise SystemExit(f"--satellite must be one of {', '.join(cfg.satellites)}")
    sat = cfg.satellites[args.satellite]
    today = args.today or datetime.datetime.now(datetime.timezone.utc).date()

    credentials = None if args.anonymous else auth.get_credentials()
    gcs = auth.storage_client(credentials, project=cfg.gee_project, anonymous=args.anonymous)
    done = state.done_days(gcs, cfg.bucket, cfg.state_prefix, sat.name)

    if args.days:
        todo = sorted(d for d in args.days if args.force or d not in done)
    else:
        todo = state.pending_days(sat, done, today, cfg.lookback_days)
    mine = state.split_for_node(todo, args.node_index, args.node_total)

    print(f"{sat.name}: today={today} processed_through={sat.processed_through} "
          f"lag={sat.lag_days}d lookback={cfg.lookback_days}d markers={len(done)}")
    print(f"{sat.name}: {len(todo)} pending day(s); node {args.node_index + 1}/{args.node_total} "
          f"takes {len(mine)}: {', '.join(d.isoformat() for d in mine) or '-'}")
    if args.dry_run:
        return 0
    if sat.name == "planet":
        cfgmod.require_env("PLANET_API_KEY")  # fail fast, before any day starts

    budget = cfg.time_budget_minutes * 60
    results = []
    for day in mine:
        elapsed = time.monotonic() - t_start
        if elapsed > budget:
            print(f"\nTime budget ({cfg.time_budget_minutes:.0f} min) used; deferring {day} to the next run")
            results.append({"date": day.isoformat(), "status": "deferred"})
            continue

        print(f"\n\n===== {sat.name} {day} =====", flush=True)
        t_day = time.monotonic()
        try:
            processor = make_processor(sat, day, cfg, credentials)
            n_images = processor.run_batch()
            seconds = time.monotonic() - t_day
            state.mark_done(gcs, cfg.bucket, cfg.state_prefix, sat.name, day, n_images, seconds)
            results.append({"date": day.isoformat(), "status": "done", "n_images": n_images,
                            "seconds": round(seconds)})
            print(f"{sat.name} {day}: done ({n_images} image(s), {seconds / 60:.1f} min)")
        except Exception as exc:
            traceback.print_exc()
            results.append({"date": day.isoformat(), "status": "failed", "error": str(exc)[:500]})
            print(f"{sat.name} {day}: FAILED, will be retried next run")

    print(f"\n===== {sat.name} summary (node {args.node_index + 1}/{args.node_total}) =====")
    for r in results:
        extra = f" images={r['n_images']}" if "n_images" in r else ""
        print(f"  {r['date']}  {r['status']}{extra}")
    if not results:
        print("  nothing to do")

    if args.summary:
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(json.dumps({"satellite": sat.name, "results": results}, indent=1))

    return 1 if any(r["status"] == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

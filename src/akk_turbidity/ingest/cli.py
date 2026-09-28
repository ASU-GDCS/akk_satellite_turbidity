"""akk-ingest: prepare -> (human approval) -> apply -> archive.

  akk-ingest prepare --out build/ingest     build candidate layer + review artifacts
  akk-ingest apply   --out build/ingest     copy candidate over the layer if the
                                            layer is unchanged since prepare
  akk-ingest archive --out build/ingest     gzip + move the manifest's source files
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from .. import auth
from .. import config as cfgmod
from . import layer


def _load_manifest(out):
    return json.loads((Path(out) / "manifest.json").read_text())


def cmd_prepare(cfg, args):
    from .prepare import prepare

    client = auth.storage_client(project=cfg.gee_project, anonymous=args.anonymous)
    prepare(cfg, client, args.out)
    return 0


def cmd_apply(cfg, args):
    manifest = _load_manifest(args.out)
    current = layer.sha256_file(cfg.layer)
    if current != manifest["base_sha256"]:
        print(f"{cfg.layer.relative_to(cfg.root)} changed since this update was prepared "
              "(someone else committed to it). Re-run the ingest pipeline.", file=sys.stderr)
        return 1
    if manifest["new_features"] == 0:
        print("No new outlines; layer left unchanged.")
        return 0
    shutil.copyfile(Path(args.out) / "turbidity_outlines.geojson", cfg.layer)

    dates = sorted({(v["date"], v["label"]) for v in manifest["vectors"]
                    if v["status"] == "new" and v["n_features"] > 0})
    first, last = dates[0][0], dates[-1][0]
    msg = [f"Add {manifest['new_features']} turbidity outlines ({first} to {last})", ""]
    msg += [f"- {d} {s}" for d, s in dates]
    if manifest.get("build_url"):
        msg += ["", f"Reviewed in {manifest['build_url']}"]
    if args.message_file:
        Path(args.message_file).write_text("\n".join(msg) + "\n")
    print(msg[0])
    return 0


def cmd_archive(cfg, args):
    from .archive import archive_manifest

    manifest = _load_manifest(args.out)
    client = auth.storage_client(project=cfg.gee_project)
    _, failures = archive_manifest(cfg, client, manifest, dry_run=args.dry_run)
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=cfgmod.DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="build candidate layer and review artifacts")
    p.add_argument("--out", default="build/ingest")
    p.add_argument("--anonymous", action="store_true",
                   help="read the (public) bucket without credentials; for local testing")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("apply", help="write the reviewed candidate over the published layer")
    p.add_argument("--out", default="build/ingest")
    p.add_argument("--message-file", default=None, help="write a git commit message here")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("archive", help="gzip + move reviewed source files to the archive prefix")
    p.add_argument("--out", default="build/ingest")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_archive)

    args = parser.parse_args(argv)
    cfg = cfgmod.load(args.config)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())

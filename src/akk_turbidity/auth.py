"""Google credentials for Earth Engine and Cloud Storage.

No key files are used. Credentials come from Application Default Credentials:

* in CircleCI, ci/gcp_wif_login.sh points GOOGLE_APPLICATION_CREDENTIALS at a
  Workload Identity Federation config that swaps the job's OIDC token for a
  short-lived token of the target service account;
* locally, run
  ``gcloud auth application-default login --impersonate-service-account=<SA>``.
"""
import argparse
import sys

import google.auth
from google.cloud import storage

SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/devstorage.read_write",
)


def get_credentials():
    credentials, _ = google.auth.default(scopes=SCOPES)
    return credentials


def init_ee(credentials, project):
    import ee

    ee.Initialize(credentials=credentials, project=project)


def storage_client(credentials=None, project=None, anonymous=False):
    """Storage client; ``anonymous`` only works for public buckets (read-only)."""
    if anonymous:
        return storage.Client.create_anonymous_client()
    if credentials is None:
        credentials = get_credentials()
    return storage.Client(credentials=credentials, project=project)


def main(argv=None):
    """Smoke test used by the CircleCI ``auth-check`` run."""
    from . import config as cfgmod

    parser = argparse.ArgumentParser(description="Check GCP credentials can reach GEE and/or the bucket")
    parser.add_argument("--config", default=cfgmod.DEFAULT_CONFIG)
    parser.add_argument("--ee", action="store_true", help="check Earth Engine access")
    parser.add_argument("--gcs", action="store_true", help="check bucket access")
    args = parser.parse_args(argv)
    cfg = cfgmod.load(args.config)

    creds = get_credentials()
    ok = True
    if args.ee:
        import ee

        try:
            init_ee(creds, cfg.gee_project)
            info = ee.data.getAsset(cfg.aoi)
            print(f"Earth Engine OK: read asset {info['name']}")
        except Exception as exc:
            ok = False
            print(f"Earth Engine FAILED: {exc}", file=sys.stderr)
    if args.gcs:
        try:
            client = storage_client(creds, project=cfg.gee_project)
            bucket = client.bucket(cfg.bucket)
            blobs = list(client.list_blobs(bucket, prefix=cfg.state_prefix.split("/")[0] + "/", max_results=1))
            print(f"GCS OK: listed gs://{cfg.bucket} ({len(blobs)} object sampled)")
            wanted = ["storage.objects.list", "storage.objects.get",
                      "storage.objects.create", "storage.objects.delete"]
            granted = bucket.test_iam_permissions(wanted)
            print("Bucket-level permissions (IAM conditions on a prefix may not show here):",
                  ", ".join(granted) or "none")
        except Exception as exc:
            ok = False
            print(f"GCS FAILED: {exc}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

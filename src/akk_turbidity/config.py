"""Load the non-secret pipeline configuration (config/pipeline.toml).

Secrets and identities are never read from here; they come from the environment
(see README "Secrets") or from Application Default Credentials.
"""
import datetime
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = "config/pipeline.toml"


@dataclass(frozen=True)
class Satellite:
    name: str
    label: str
    prefix: str
    baselines: str
    lag_days: int
    processed_through: datetime.date
    daily_collection: str | None = None
    local_aoi: Path | None = None


@dataclass(frozen=True)
class Config:
    root: Path
    gee_project: str
    assets_root: str
    aoi: str
    depth: str
    bucket: str
    state_prefix: str
    archive_prefix: str
    lookback_days: int
    time_budget_minutes: float
    satellites: dict[str, Satellite]
    layer: Path
    layer_name: str

    def satellite_by_label(self, label: str) -> Satellite:
        for sat in self.satellites.values():
            if sat.label == label:
                return sat
        raise KeyError(label)


def load(path: str | os.PathLike = DEFAULT_CONFIG) -> Config:
    path = Path(path).resolve()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    # config/pipeline.toml lives one level below the repository root
    root = path.parent.parent

    sats = {}
    for name, s in raw["satellites"].items():
        sats[name] = Satellite(
            name=name,
            label=s["label"],
            prefix=s["prefix"],
            baselines=s["baselines"],
            lag_days=int(s["lag_days"]),
            processed_through=datetime.date.fromisoformat(s["processed_through"]),
            daily_collection=s.get("daily_collection"),
            local_aoi=root / s["local_aoi"] if "local_aoi" in s else None,
        )

    return Config(
        root=root,
        gee_project=raw["gee"]["project"],
        assets_root=raw["gee"]["assets_root"].rstrip("/"),
        aoi=raw["gee"]["aoi"],
        depth=raw["gee"]["depth"],
        bucket=raw["gcs"]["bucket"],
        state_prefix=raw["gcs"]["state_prefix"].rstrip("/"),
        archive_prefix=raw["gcs"]["archive_prefix"].rstrip("/"),
        lookback_days=int(raw["monitor"]["lookback_days"]),
        time_budget_minutes=float(raw["monitor"]["time_budget_minutes"]),
        satellites=sats,
        layer=root / raw["ingest"]["layer"],
        layer_name=raw["ingest"]["layer_name"],
    )


def require_env(name: str) -> str:
    """Return a required environment variable without ever echoing its value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable {name} (see README 'Secrets')")
    return value

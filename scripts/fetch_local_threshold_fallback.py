#!/usr/bin/env python3
"""Build a local empirical flash-flood-threshold fallback layer.

This is NOT official Flash Flood Guidance.

It consumes the existing threshold grid from mefferso/Flash-Flood-Guidance-Map
and converts point thresholds into small square polygons with ffg-like fields so
that the fusion pipeline can keep moving while the live RFC/FFG source is being
sorted out.

The output is clearly marked as source='local_empirical_threshold_fallback'.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DEFAULT_URL = "https://raw.githubusercontent.com/mefferso/Flash-Flood-Guidance-Map/main/docs/data/threshold_grid.geojson"


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def get_json(url: str) -> Any:
    r = requests.get(url, timeout=90, headers={"User-Agent": "lix-flood-risk-fusion/0.1"})
    r.raise_for_status()
    return r.json()


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def box(lon: float, lat: float, half_size: float) -> list[list[list[float]]]:
    west = lon - half_size
    east = lon + half_size
    south = lat - half_size
    north = lat + half_size
    return [[[west, south], [east, south], [east, north], [west, north], [west, south]]]


def choose_threshold(props: dict) -> float | None:
    for key in [
        "threshold_3h_in",
        "weighted_mean_3h_in",
        "median_3h_in",
        "mean_3h_in",
        "min_3h_in",
    ]:
        val = as_float(props.get(key))
        if val is not None and 0 < val <= 25:
            return val
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default="data/ffg/ffg.geojson")
    ap.add_argument("--flood-prone-out", default="data/flood_prone/flood_prone.geojson")
    ap.add_argument("--debug-json", default="docs/data/local_threshold_fallback_debug.json")
    ap.add_argument("--cell-size-deg", type=float, default=0.10, help="Square polygon size in degrees around each threshold point")
    ap.add_argument("--min-threshold", type=float, default=0.01)
    args = ap.parse_args()

    src = get_json(args.url)
    half = args.cell_size_deg / 2.0
    threshold_features = []
    prone_features = []
    skipped = 0

    for feat in src.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            skipped += 1
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            skipped += 1
            continue
        lon, lat = as_float(coords[0]), as_float(coords[1])
        if lon is None or lat is None:
            skipped += 1
            continue
        props = feat.get("properties") or {}
        thresh = choose_threshold(props)
        if thresh is None or thresh < args.min_threshold:
            skipped += 1
            continue

        poly_geom = {"type": "Polygon", "coordinates": box(lon, lat, half)}
        base_props = dict(props)
        base_props.update({
            "name": "Local empirical flash flood threshold cell",
            "source": "local_empirical_threshold_fallback",
            "source_url": args.url,
            "threshold_duration_hr": 3,
            "threshold_3hr_in": round(thresh, 3),
            "note": "Not official RFC Flash Flood Guidance. Derived from local empirical threshold grid for pipeline fallback/testing.",
        })

        ffg_props = dict(base_props)
        # This intentionally duplicates the local 3-hr threshold into ffg_6hr so
        # the current 6-hr fusion script can run. It is labeled as fallback data.
        ffg_props.update({
            "ffg_1hr": round(max(thresh * 0.55, 0.01), 3),
            "ffg_3hr": round(thresh, 3),
            "ffg_6hr": round(thresh, 3),
            "ffg_field_warning": "ffg_6hr is a local 3-hr empirical threshold copied for temporary 6-hr fusion testing, not official 6-hr FFG.",
        })
        threshold_features.append({"type": "Feature", "properties": ffg_props, "geometry": poly_geom})

        prone_props = dict(base_props)
        prone_props.update({"susceptibility": "local empirical threshold", "threshold_3h_in": round(thresh, 3)})
        prone_features.append({"type": "Feature", "properties": prone_props, "geometry": poly_geom})

    now = datetime.now(timezone.utc).isoformat()
    threshold_fc = {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": now,
            "source": args.url,
            "method": "Converted local empirical threshold grid points to square polygons for fallback fusion testing",
            "official_ffg": False,
            "feature_count": len(threshold_features),
        },
        "features": threshold_features,
    }
    prone_fc = {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": now,
            "source": args.url,
            "method": "Converted local empirical threshold grid points to flood-prone polygons",
            "feature_count": len(prone_features),
        },
        "features": prone_features,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(threshold_fc, indent=2), encoding="utf-8")
    Path(args.flood_prone_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.flood_prone_out).write_text(json.dumps(prone_fc, indent=2), encoding="utf-8")

    debug = {
        "generated_utc": now,
        "status": "success" if threshold_features else "no usable threshold features",
        "source": args.url,
        "threshold_feature_count": len(threshold_features),
        "flood_prone_feature_count": len(prone_features),
        "skipped_count": skipped,
        "official_ffg": False,
        "warning": "This fallback is not official RFC FFG. It is a local empirical threshold layer for testing the fusion pipeline.",
    }
    Path(args.debug_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.debug_json).write_text(json.dumps(debug, indent=2), encoding="utf-8")
    log(f"Wrote {args.out} with {len(threshold_features)} fallback threshold feature(s)")
    log(f"Wrote {args.flood_prone_out} with {len(prone_features)} fallback flood-prone feature(s)")
    return 0 if threshold_features else 1


if __name__ == "__main__":
    raise SystemExit(main())

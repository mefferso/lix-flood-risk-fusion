#!/usr/bin/env python3
"""Normalize any county/parish/basin FFG GeoJSON into the fields this project expects.

Example:
  python scripts/normalize_ffg_geojson.py \
    --input raw_ffg.geojson \
    --out data/ffg/ffg.geojson \
    --ffg-1hr-field FFG1 \
    --ffg-3hr-field FFG3 \
    --ffg-6hr-field FFG6
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def as_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="data/ffg/ffg.geojson")
    ap.add_argument("--ffg-1hr-field", default=None)
    ap.add_argument("--ffg-3hr-field", default=None)
    ap.add_argument("--ffg-6hr-field", default=None)
    ap.add_argument("--name-field", default=None)
    ap.add_argument("--valid-time", default=None)
    args = ap.parse_args()

    src = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out_features = []

    for feat in src.get("features", []):
        props = feat.get("properties") or {}
        new_props = dict(props)

        if args.ffg_1hr_field:
            new_props["ffg_1hr"] = as_float(props.get(args.ffg_1hr_field))
        if args.ffg_3hr_field:
            new_props["ffg_3hr"] = as_float(props.get(args.ffg_3hr_field))
        if args.ffg_6hr_field:
            new_props["ffg_6hr"] = as_float(props.get(args.ffg_6hr_field))
        if args.name_field:
            new_props["name"] = props.get(args.name_field)
        if args.valid_time:
            new_props["valid_time_utc"] = args.valid_time

        out_features.append({
            "type": "Feature",
            "properties": new_props,
            "geometry": feat.get("geometry"),
        })

    out = {"type": "FeatureCollection", "features": out_features}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} with {len(out_features)} features")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create tiny placeholder FFG and flood-prone GeoJSON inputs.

These are NOT meteorologically meaningful. They only let the first pipeline run.
Replace these with real FFG and real flood-prone polygons.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def box(west: float, south: float, east: float, north: float):
    return [[
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
    ]]


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-if-missing", action="store_true")
    args = ap.parse_args()

    ffg_path = Path("data/ffg/ffg.geojson")
    prone_path = Path("data/flood_prone/flood_prone.geojson")

    if not args.only_if_missing or not ffg_path.exists():
        ffg = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Sample New Orleans metro FFG zone",
                        "ffg_1hr": 2.0,
                        "ffg_3hr": 3.0,
                        "ffg_6hr": 4.0,
                        "source": "placeholder",
                    },
                    "geometry": {"type": "Polygon", "coordinates": box(-90.55, 29.70, -89.70, 30.25)},
                },
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Sample Baton Rouge metro FFG zone",
                        "ffg_1hr": 2.2,
                        "ffg_3hr": 3.3,
                        "ffg_6hr": 4.5,
                        "source": "placeholder",
                    },
                    "geometry": {"type": "Polygon", "coordinates": box(-91.45, 30.20, -90.85, 30.75)},
                },
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Sample Northshore FFG zone",
                        "ffg_1hr": 2.4,
                        "ffg_3hr": 3.6,
                        "ffg_6hr": 5.0,
                        "source": "placeholder",
                    },
                    "geometry": {"type": "Polygon", "coordinates": box(-90.50, 30.20, -89.60, 30.75)},
                },
            ],
        }
        write_json(ffg_path, ffg)
        print(f"Wrote {ffg_path}")

    if not args.only_if_missing or not prone_path.exists():
        prone = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Sample flood-prone New Orleans / Jefferson / St Bernard", "susceptibility": "high"},
                    "geometry": {"type": "Polygon", "coordinates": box(-90.35, 29.78, -89.78, 30.08)},
                },
                {
                    "type": "Feature",
                    "properties": {"name": "Sample flood-prone Baton Rouge", "susceptibility": "moderate"},
                    "geometry": {"type": "Polygon", "coordinates": box(-91.25, 30.33, -90.95, 30.58)},
                },
                {
                    "type": "Feature",
                    "properties": {"name": "Sample flood-prone Slidell / Pearl River", "susceptibility": "moderate"},
                    "geometry": {"type": "Polygon", "coordinates": box(-90.05, 30.15, -89.62, 30.45)},
                },
            ],
        }
        write_json(prone_path, prone)
        print(f"Wrote {prone_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

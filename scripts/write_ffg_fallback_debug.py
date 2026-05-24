#!/usr/bin/env python3
"""Write fallback FFG debug artifacts when the live FFG pull fails."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ARCGIS_SERVICE = "https://idpgis.ncep.noaa.gov/arcgis/rest/services/NWS_Forecasts_Guidance_Warnings/rfc_gridded_ffg/MapServer"


def write_json_if_missing(path: Path, obj: dict) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="live FFG fetch failed")
    args = ap.parse_args()

    out_dir = Path("docs/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    arcgis_debug_path = out_dir / "ffg_arcgis_debug.json"
    write_json_if_missing(
        arcgis_debug_path,
        {
            "generated_utc": now,
            "status": "ArcGIS FFG fetch failed before debug output was created",
            "fallback": "kept existing data/ffg/ffg.geojson",
            "attempted_source": ARCGIS_SERVICE,
            "note": "This file exists so GitHub Pages has an ArcGIS FFG debug artifact even when the service request fails early.",
        },
    )

    raw_path = out_dir / "latest_ffg_product.txt"
    if not raw_path.exists():
        raw_path.write_text(
            "FFG text fetch/parse failed before a raw text product could be saved.\n"
            "The workflow kept the existing data/ffg/ffg.geojson file so the map could still build.\n",
            encoding="utf-8",
        )

    parse_debug_path = out_dir / "ffg_parse_debug.json"
    write_json_if_missing(
        parse_debug_path,
        {
            "generated_utc": now,
            "status": "text FFG fetch or parse failed",
            "fallback": "kept existing data/ffg/ffg.geojson",
            "attempted_sources": [
                "https://api.weather.gov/products/types/FFG/locations/ORN",
                "https://mesonet.agron.iastate.edu/wx/afos/p.php?pil=FFGORN",
            ],
            "note": "This file exists so GitHub Pages has a debug artifact even when the live FFG pull fails.",
        },
    )

    status_path = out_dir / "ffg_fetch_status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_utc": now,
                "status": args.reason,
                "arcgis_debug_file": "ffg_arcgis_debug.json",
                "text_debug_file": "ffg_parse_debug.json",
                "fallback": "kept existing data/ffg/ffg.geojson",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

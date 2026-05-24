#!/usr/bin/env python3
"""Write fallback FFG debug artifacts when the live FFG pull fails."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    out_dir = Path("docs/data")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "latest_ffg_product.txt"
    if not raw_path.exists():
        raw_path.write_text(
            "FFG fetch/parse failed before a raw text product could be saved.\n"
            "The workflow kept the existing data/ffg/ffg.geojson file so the map could still build.\n",
            encoding="utf-8",
        )

    debug_path = out_dir / "ffg_parse_debug.json"
    if not debug_path.exists():
        debug_path.write_text(
            json.dumps(
                {
                    "generated_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "live FFG fetch or parse failed",
                    "fallback": "kept existing data/ffg/ffg.geojson",
                    "attempted_sources": [
                        "https://api.weather.gov/products/types/FFG/locations/ORN",
                        "https://mesonet.agron.iastate.edu/wx/afos/p.php?pil=FFGORN",
                    ],
                    "note": "This file exists so GitHub Pages has a debug artifact even when the live FFG pull fails.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convert production FFG legend-bin codes to inch estimates.

The production raster identify response currently gives a class/bin number,
not a raw rainfall amount. This post-processes the generated ffg.geojson and
replaces the temporary class number with a midpoint/lower-bound estimate from
the published legend bins.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Assumption from the service legend order: code 1 is the first legend entry.
# 1: >=5, 2: 4-5, 3: 3-4, 4: 2.5-3, 5: 2-2.5,
# 6: 1.5-2, 7: 1-1.5, 8: 0.75-1, 9: 0.5-0.75,
# 10: 0.25-0.5, 11: <0.25.
BIN_MAP: dict[int, dict[str, Any]] = {
    1: {"label": ">= 5 inches", "low": 5.0, "high": None, "estimate": 5.0},
    2: {"label": "4 to 5 inches", "low": 4.0, "high": 5.0, "estimate": 4.5},
    3: {"label": "3 to 4 inches", "low": 3.0, "high": 4.0, "estimate": 3.5},
    4: {"label": "2.5 to 3 inches", "low": 2.5, "high": 3.0, "estimate": 2.75},
    5: {"label": "2.0 to 2.5 inches", "low": 2.0, "high": 2.5, "estimate": 2.25},
    6: {"label": "1.5 to 2 inches", "low": 1.5, "high": 2.0, "estimate": 1.75},
    7: {"label": "1 to 1.5 inches", "low": 1.0, "high": 1.5, "estimate": 1.25},
    8: {"label": "0.75 to 1 inches", "low": 0.75, "high": 1.0, "estimate": 0.875},
    9: {"label": "0.5 to 0.75 inches", "low": 0.5, "high": 0.75, "estimate": 0.625},
    10: {"label": "0.25 to 0.5 inches", "low": 0.25, "high": 0.5, "estimate": 0.375},
    11: {"label": "< 0.25 inches", "low": 0.0, "high": 0.25, "estimate": 0.125},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="data/ffg/ffg.geojson")
    ap.add_argument("--debug-json", default="docs/data/ffg_bin_conversion_debug.json")
    args = ap.parse_args()

    p = Path(args.path)
    fc = json.loads(p.read_text(encoding="utf-8"))
    changed = 0
    skipped = 0
    counts: dict[str, int] = {}

    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        duration = int(props.get("duration_hr") or fc.get("metadata", {}).get("duration_hr") or 6)
        key = f"ffg_{duration}hr"
        field = str(props.get("source_value_field") or "")
        if "class" not in field.lower():
            skipped += 1
            continue
        try:
            code = int(round(float(props.get(key))))
        except Exception:
            skipped += 1
            continue
        info = BIN_MAP.get(code)
        if not info:
            skipped += 1
            continue
        estimate = float(info["estimate"])
        props["raw_ffg_class_code"] = code
        props["ffg_bin_label"] = info["label"]
        props["ffg_bin_low_in"] = info["low"]
        props["ffg_bin_high_in"] = info["high"]
        props["ffg_estimate_method"] = "legend_bin_estimate"
        props[key] = round(estimate, 3)
        if duration == 1:
            props["ffg_1hr"] = round(estimate, 3)
        elif duration == 3:
            props["ffg_3hr"] = round(estimate, 3)
        elif duration == 6:
            props["ffg_6hr"] = round(estimate, 3)
        counts[str(code)] = counts.get(str(code), 0) + 1
        changed += 1

    meta = fc.setdefault("metadata", {})
    meta["value_note"] = "FFG values are estimated from service legend bins, not continuous raw raster pixel values."
    meta["bin_conversion_applied"] = True
    meta["bin_conversion_changed_count"] = changed

    p.write_text(json.dumps(fc, indent=2), encoding="utf-8")
    debug = {"status": "success", "path": args.path, "changed": changed, "skipped": skipped, "class_counts": counts, "bin_map": BIN_MAP}
    Path(args.debug_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.debug_json).write_text(json.dumps(debug, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

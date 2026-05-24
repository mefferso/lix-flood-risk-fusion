#!/usr/bin/env python3
"""Build flood-risk fusion output for multiple HREF 6-hour periods.

Keeps FFG and flood-prone inputs constant, then repeats the fusion against
sequential HREF accumulation periods from the latest run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fuse_flash_flood_risk as fuse


def latest_run_layers(catalog: dict, duration: int, count: int, start_forecast_hour: int | None = None) -> list[dict]:
    layers = [l for l in catalog.get("layers", []) if int(l.get("accumHours", duration)) == duration]
    if not layers:
        raise RuntimeError(f"No HREF layers found for duration={duration}")

    latest_run = max(str(l.get("run", "")) for l in layers)
    layers = [l for l in layers if str(l.get("run", "")) == latest_run]
    if start_forecast_hour is not None:
        layers = [l for l in layers if int(l.get("forecastHour", -1)) >= start_forecast_hour]

    layers.sort(key=lambda x: int(x.get("forecastHour", 9999)))
    if len(layers) < count:
        raise RuntimeError(f"Only found {len(layers)} layers for latest run={latest_run}, duration={duration}; requested {count}")
    return layers[:count]


def write_period_outputs(
    layer: dict,
    period_index: int,
    args: argparse.Namespace,
    ffg_lookup: fuse.SpatialLookup,
    prone_lookup: fuse.SpatialLookup,
) -> dict[str, Any]:
    vg_url = fuse.value_grid_url(args.href_catalog_url, layer["valueGridUrl"])
    fuse.log(f"Period {period_index}: using HREF layer {layer.get('id')} / {layer.get('periodLabel')}")
    grid = fuse.read_json(vg_url)

    raw_features = fuse.build_features(
        grid=grid,
        ffg_lookup=ffg_lookup,
        prone_lookup=prone_lookup,
        duration=args.duration,
        require_flood_prone=not args.no_require_flood_prone,
        min_qpf=args.min_qpf,
    )
    dissolved = fuse.dissolve_features(raw_features, layer)

    generated = datetime.now(timezone.utc).isoformat()
    metadata = {
        "generated_utc": generated,
        "method": "HREF QPF / FFG ratio intersected with local flood-prone polygons",
        "duration_hr": args.duration,
        "period_index": period_index,
        "risk_thresholds": {
            "Possible": "qpf/ffg >= 0.75",
            "Probable": "qpf/ffg >= 1.00",
            "Significant": "qpf/ffg >= 1.50",
        },
        "href_layer": layer,
    }

    risk_fc = {"type": "FeatureCollection", "metadata": metadata, "features": dissolved}
    raw_fc = {"type": "FeatureCollection", "metadata": metadata, "features": raw_features}

    suffix = f"_p{period_index:02d}"
    risk_path = Path(args.out_dir) / f"flash_flood_risk{suffix}.geojson"
    raw_path = Path(args.out_dir) / f"flash_flood_risk_raw_cells{suffix}.geojson"
    fuse.write_json(risk_path, risk_fc)
    fuse.write_json(raw_path, raw_fc)

    # Backward-compatible files for period 1.
    if period_index == 1:
        fuse.write_json(Path(args.out_dir) / "flash_flood_risk.geojson", risk_fc)
        fuse.write_json(Path(args.out_dir) / "flash_flood_risk_raw_cells.geojson", raw_fc)

    return {
        "period_index": period_index,
        "label": f"Period {period_index}: {layer.get('periodLabel')}",
        "href_layer_id": layer.get("id"),
        "href_period": layer.get("periodLabel"),
        "href_run": layer.get("runLabel"),
        "forecast_hour": layer.get("forecastHour"),
        "valid_time_utc": layer.get("validTimeUTC"),
        "href_value_grid_url": vg_url,
        "risk_url": f"data/{risk_path.name}",
        "raw_url": f"data/{raw_path.name}",
        "risk_feature_count": len(dissolved),
        "raw_cell_count": len(raw_features),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--href-catalog-url", required=True)
    ap.add_argument("--ffg", required=True)
    ap.add_argument("--flood-prone", required=True)
    ap.add_argument("--duration", type=int, default=6, choices=[1, 3, 6])
    ap.add_argument("--period-count", type=int, default=4)
    ap.add_argument("--start-forecast-hour", type=int, default=None)
    ap.add_argument("--min-qpf", type=float, default=0.25)
    ap.add_argument("--no-require-flood-prone", action="store_true")
    ap.add_argument("--out-dir", default="docs/data")
    ap.add_argument("--manifest-out", default="docs/data/manifest.json")
    ap.add_argument("--periods-manifest-out", default="docs/data/periods_manifest.json")
    args = ap.parse_args()

    fuse.log(f"Loading HREF catalog: {args.href_catalog_url}")
    catalog = fuse.read_json(args.href_catalog_url)
    layers = latest_run_layers(catalog, args.duration, args.period_count, args.start_forecast_hour)

    fuse.log(f"Loading FFG polygons: {args.ffg}")
    ffg_lookup = fuse.SpatialLookup(fuse.load_fc(args.ffg))
    fuse.log(f"Loaded {len(ffg_lookup.geoms)} FFG geometries")

    fuse.log(f"Loading flood-prone polygons: {args.flood_prone}")
    prone_lookup = fuse.SpatialLookup(fuse.load_fc(args.flood_prone))
    fuse.log(f"Loaded {len(prone_lookup.geoms)} flood-prone geometries")

    periods = []
    for idx, layer in enumerate(layers, start=1):
        periods.append(write_period_outputs(layer, idx, args, ffg_lookup, prone_lookup))

    generated = datetime.now(timezone.utc).isoformat()
    first = periods[0]
    manifest = {
        "generated_utc": generated,
        "href_catalog_url": args.href_catalog_url,
        "href_value_grid_url": first["href_value_grid_url"],
        "href_layer_id": first["href_layer_id"],
        "href_period": first["href_period"],
        "href_run": first["href_run"],
        "duration_hr": args.duration,
        "risk_feature_count": first["risk_feature_count"],
        "raw_cell_count": first["raw_cell_count"],
        "ffg_path": args.ffg,
        "flood_prone_path": args.flood_prone,
        "multi_period": True,
        "period_count": len(periods),
        "periods_manifest": "data/periods_manifest.json",
    }
    periods_manifest = {
        "generated_utc": generated,
        "href_catalog_url": args.href_catalog_url,
        "href_run": first["href_run"],
        "duration_hr": args.duration,
        "period_count": len(periods),
        "ffg_path": args.ffg,
        "flood_prone_path": args.flood_prone,
        "periods": periods,
    }

    fuse.write_json(args.manifest_out, manifest)
    fuse.write_json(args.periods_manifest_out, periods_manifest)
    fuse.log(f"Wrote {args.periods_manifest_out} with {len(periods)} period(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

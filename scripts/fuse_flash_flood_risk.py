#!/usr/bin/env python3
"""Fuse forecast QPF, FFG, and local flood-prone polygons into risk polygons.

Input:
  - HREF value grid JSON produced by href-qpf-viewer
  - FFG GeoJSON with ffg_6hr, ffg_3hr, or ffg_1hr fields
  - Flood-prone GeoJSON polygons

Output:
  - Dissolved risk polygons
  - Optional raw selected grid-cell polygons
  - Manifest with metadata
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import numpy as np
import requests
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree


MISSING_DEFAULT = -9999


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def read_bytes(path_or_url: str) -> bytes:
    if path_or_url.startswith(("http://", "https://")):
        r = requests.get(path_or_url, timeout=60, headers={"User-Agent": "lix-flood-risk-fusion/0.1"})
        r.raise_for_status()
        return r.content
    return Path(path_or_url).read_bytes()


def read_json(path_or_url: str) -> Any:
    data = read_bytes(path_or_url)
    if path_or_url.endswith(".gz"):
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def pick_href_layer(catalog: dict, duration: int, forecast_hour: int | None):
    layers = catalog.get("layers") or []
    candidates = [l for l in layers if int(l.get("accumHours", duration)) == duration]
    if forecast_hour is not None:
        candidates = [l for l in candidates if int(l.get("forecastHour", -1)) == forecast_hour]
    if not candidates:
        raise RuntimeError(f"No HREF layers found for duration={duration}, forecast_hour={forecast_hour}")
    candidates.sort(key=lambda x: (x.get("run", ""), int(x.get("forecastHour", 999))))
    return candidates[0]


def get_catalog_base(catalog_url: str) -> str:
    if catalog_url.startswith(("http://", "https://")):
        return catalog_url.rsplit("/", 1)[0] + "/"
    return str(Path(catalog_url).parent) + "/"


def href_asset_url(catalog_url: str, rel: str) -> str:
    """Resolve asset paths from the href-qpf-viewer catalog.

    Important gotcha: href-qpf-viewer publishes catalog.json at:

        .../href-qpf-viewer/data/catalog.json

    but each layer URL inside that catalog is written from the GitHub Pages
    document root, for example:

        data/value_grids/<layer>.json.gz

    If we naively join that path to the catalog directory, we get the bad URL:

        .../href-qpf-viewer/data/data/value_grids/<layer>.json.gz

    This function treats paths beginning with "data/" as relative to the Pages
    root instead of relative to catalog.json's own data/ directory.
    """
    if rel.startswith(("http://", "https://")):
        return rel

    if catalog_url.startswith(("http://", "https://")):
        base = get_catalog_base(catalog_url)
        if rel.startswith("data/") and base.rstrip("/").endswith("/data"):
            return urljoin(base, "../" + rel)
        return urljoin(base, rel)

    catalog_path = Path(catalog_url)
    base_path = catalog_path.parent
    if rel.startswith("data/") and base_path.name == "data":
        return str(base_path.parent / rel)
    return str(base_path / rel)


def value_grid_url(catalog_url: str, rel: str) -> str:
    return href_asset_url(catalog_url, rel)


def load_fc(path: str) -> list[dict]:
    fc = read_json(path)
    if fc.get("type") != "FeatureCollection":
        raise RuntimeError(f"{path} is not a FeatureCollection")
    return fc.get("features") or []


def clean_geom(feat: dict):
    geom = feat.get("geometry")
    if not geom:
        return None
    try:
        g = shape(geom)
        if g.is_empty:
            return None
        if not g.is_valid:
            g = g.buffer(0)
        return g
    except Exception:
        return None


class SpatialLookup:
    def __init__(self, features: list[dict]):
        self.items = []
        for feat in features:
            geom = clean_geom(feat)
            if geom is None:
                continue
            self.items.append((geom, feat.get("properties") or {}))

        self.geoms = [x[0] for x in self.items]
        self.props = [x[1] for x in self.items]
        self.tree = STRtree(self.geoms) if self.geoms else None

    def containing_props(self, point: Point) -> list[dict]:
        if self.tree is None:
            return []
        hits = self.tree.query(point)
        out = []
        for hit in hits:
            # Shapely 2 returns indices; older Shapely may return geometries.
            idx = int(hit) if not hasattr(hit, "contains") else self.geoms.index(hit)
            geom = self.geoms[idx]
            if geom.contains(point) or geom.touches(point):
                out.append(self.props[idx])
        return out

    def contains(self, point: Point) -> bool:
        return bool(self.containing_props(point))


def parse_float(v):
    if v is None:
        return None
    try:
        x = float(str(v).replace(",", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def ffg_value(props: dict, duration: int):
    candidates = [
        f"ffg_{duration}hr",
        f"ffg{duration}hr",
        f"ffg_{duration}h",
        f"ffg{duration}h",
        f"FFG_{duration}HR",
        f"FFG{duration}HR",
        f"FFG{duration}",
        "ffg",
        "value",
    ]
    for key in candidates:
        if key in props:
            val = parse_float(props.get(key))
            if val is not None:
                return val
    return None


def category_from_ratio(ratio: float) -> str | None:
    if ratio >= 1.50:
        return "Significant"
    if ratio >= 1.00:
        return "Probable"
    if ratio >= 0.75:
        return "Possible"
    return None


def rgba_category_color(category: str) -> str:
    return {
        "Significant": "#dc2626",
        "Probable": "#f97316",
        "Possible": "#facc15",
    }.get(category, "#64748b")


def make_cell_polygon(lon: float, lat: float, dx: float, dy: float) -> Polygon:
    return Polygon([
        (lon - dx / 2, lat - dy / 2),
        (lon + dx / 2, lat - dy / 2),
        (lon + dx / 2, lat + dy / 2),
        (lon - dx / 2, lat + dy / 2),
        (lon - dx / 2, lat - dy / 2),
    ])


def build_features(
    grid: dict,
    ffg_lookup: SpatialLookup,
    prone_lookup: SpatialLookup,
    duration: int,
    require_flood_prone: bool,
    min_qpf: float,
):
    bounds = grid["bounds"]
    width = int(grid["width"])
    height = int(grid["height"])
    missing = float(grid.get("missing", MISSING_DEFAULT))
    values = np.array(grid["values"], dtype=float).reshape((height, width))

    west = float(bounds["west"])
    east = float(bounds["east"])
    south = float(bounds["south"])
    north = float(bounds["north"])

    dx = (east - west) / width
    dy = (north - south) / height

    raw_features = []

    for row in range(height):
        lat = north - (row + 0.5) * dy
        for col in range(width):
            qpf = float(values[row, col])
            if not math.isfinite(qpf) or qpf == missing or qpf < min_qpf:
                continue

            lon = west + (col + 0.5) * dx
            pt = Point(lon, lat)

            prone = prone_lookup.contains(pt)
            if require_flood_prone and not prone:
                continue

            ffg_props_list = ffg_lookup.containing_props(pt)
            if not ffg_props_list:
                continue

            # If multiple polygons overlap, use the lowest FFG. Conservative, but transparent.
            ffg_vals = [ffg_value(p, duration) for p in ffg_props_list]
            ffg_vals = [v for v in ffg_vals if v is not None and v > 0]
            if not ffg_vals:
                continue

            ffg = min(ffg_vals)
            ratio = qpf / ffg
            category = category_from_ratio(ratio)
            if not category:
                continue

            poly = make_cell_polygon(lon, lat, dx, dy)
            raw_features.append({
                "type": "Feature",
                "properties": {
                    "category": category,
                    "qpf_in": round(qpf, 3),
                    "ffg_in": round(ffg, 3),
                    "qpf_ffg_ratio": round(ratio, 3),
                    "duration_hr": duration,
                    "flood_prone": prone,
                },
                "geometry": mapping(poly),
            })

    return raw_features


def dissolve_features(raw_features: list[dict], layer: dict) -> list[dict]:
    by_cat = defaultdict(list)
    stats = defaultdict(lambda: {"max_qpf": 0.0, "min_ffg": 9999.0, "max_ratio": 0.0, "count": 0})

    for feat in raw_features:
        cat = feat["properties"]["category"]
        geom = clean_geom(feat)
        if geom is None:
            continue
        by_cat[cat].append(geom)
        p = feat["properties"]
        stats[cat]["max_qpf"] = max(stats[cat]["max_qpf"], float(p["qpf_in"]))
        stats[cat]["min_ffg"] = min(stats[cat]["min_ffg"], float(p["ffg_in"]))
        stats[cat]["max_ratio"] = max(stats[cat]["max_ratio"], float(p["qpf_ffg_ratio"]))
        stats[cat]["count"] += 1

    order = ["Possible", "Probable", "Significant"]
    out = []
    for cat in order:
        geoms = by_cat.get(cat) or []
        if not geoms:
            continue
        dissolved = unary_union(geoms)
        # Split MultiPolygon into individual features for Leaflet popup sanity.
        parts = list(dissolved.geoms) if hasattr(dissolved, "geoms") else [dissolved]
        for i, part in enumerate(parts, start=1):
            if part.is_empty:
                continue
            s = stats[cat]
            out.append({
                "type": "Feature",
                "properties": {
                    "category": cat,
                    "color": rgba_category_color(cat),
                    "max_qpf_in": round(s["max_qpf"], 3),
                    "min_ffg_in": round(s["min_ffg"], 3) if s["min_ffg"] < 9999 else None,
                    "max_qpf_ffg_ratio": round(s["max_ratio"], 3),
                    "cell_count": s["count"],
                    "href_layer_id": layer.get("id"),
                    "href_period": layer.get("periodLabel"),
                    "href_run": layer.get("runLabel"),
                    "valid_time_utc": layer.get("validTimeUTC"),
                    "part": i,
                },
                "geometry": mapping(part),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--href-catalog-url", required=True)
    ap.add_argument("--ffg", required=True)
    ap.add_argument("--flood-prone", required=True)
    ap.add_argument("--duration", type=int, default=6, choices=[1, 3, 6])
    ap.add_argument("--forecast-hour", type=int, default=None)
    ap.add_argument("--min-qpf", type=float, default=0.25)
    ap.add_argument("--no-require-flood-prone", action="store_true")
    ap.add_argument("--out", default="docs/data/flash_flood_risk.geojson")
    ap.add_argument("--raw-out", default="docs/data/flash_flood_risk_raw_cells.geojson")
    ap.add_argument("--manifest-out", default="docs/data/manifest.json")
    args = ap.parse_args()

    log(f"Loading HREF catalog: {args.href_catalog_url}")
    catalog = read_json(args.href_catalog_url)
    layer = pick_href_layer(catalog, args.duration, args.forecast_hour)
    vg_url = value_grid_url(args.href_catalog_url, layer["valueGridUrl"])
    log(f"Using HREF layer {layer.get('id')} / {layer.get('periodLabel')}")
    log(f"Loading HREF value grid: {vg_url}")
    grid = read_json(vg_url)

    log(f"Loading FFG polygons: {args.ffg}")
    ffg_lookup = SpatialLookup(load_fc(args.ffg))
    log(f"Loaded {len(ffg_lookup.geoms)} FFG geometries")

    log(f"Loading flood-prone polygons: {args.flood_prone}")
    prone_lookup = SpatialLookup(load_fc(args.flood_prone))
    log(f"Loaded {len(prone_lookup.geoms)} flood-prone geometries")

    raw_features = build_features(
        grid=grid,
        ffg_lookup=ffg_lookup,
        prone_lookup=prone_lookup,
        duration=args.duration,
        require_flood_prone=not args.no_require_flood_prone,
        min_qpf=args.min_qpf,
    )
    log(f"Selected {len(raw_features)} raw exceedance/susceptibility grid cells")

    dissolved = dissolve_features(raw_features, layer)
    log(f"Dissolved to {len(dissolved)} risk polygon feature(s)")

    risk_fc = {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "method": "HREF QPF / FFG ratio intersected with local flood-prone polygons",
            "duration_hr": args.duration,
            "risk_thresholds": {
                "Possible": "qpf/ffg >= 0.75",
                "Probable": "qpf/ffg >= 1.00",
                "Significant": "qpf/ffg >= 1.50",
            },
            "href_layer": layer,
        },
        "features": dissolved,
    }

    raw_fc = {
        "type": "FeatureCollection",
        "metadata": risk_fc["metadata"],
        "features": raw_features,
    }

    manifest = {
        "generated_utc": risk_fc["metadata"]["generated_utc"],
        "href_catalog_url": args.href_catalog_url,
        "href_value_grid_url": vg_url,
        "href_layer_id": layer.get("id"),
        "href_period": layer.get("periodLabel"),
        "href_run": layer.get("runLabel"),
        "duration_hr": args.duration,
        "risk_feature_count": len(dissolved),
        "raw_cell_count": len(raw_features),
        "ffg_path": args.ffg,
        "flood_prone_path": args.flood_prone,
    }

    write_json(args.out, risk_fc)
    write_json(args.raw_out, raw_fc)
    write_json(args.manifest_out, manifest)

    log(f"Wrote {args.out}")
    log(f"Wrote {args.raw_out}")
    log(f"Wrote {args.manifest_out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise

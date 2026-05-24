#!/usr/bin/env python3
"""Fetch official RFC gridded FFG from the production NWS MapServer.

Uses the working production endpoint:
https://mapservices.weather.noaa.gov/raster/rest/services/precip/rfc_gridded_ffg/MapServer

For now this samples the raster with the ArcGIS identify endpoint and writes
small GeoJSON cells with ffg_1hr/ffg_3hr/ffg_6hr fields.  This is an official
FFG source, but the sampling approach is still a pragmatic bridge until a true
raster/WCS/GeoTIFF numeric extraction path is added.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

SERVICE_URL = "https://mapservices.weather.noaa.gov/raster/rest/services/precip/rfc_gridded_ffg/MapServer"
MOSAIC_LAYER = {1: 0, 3: 4, 6: 8, 12: 12, 24: 16}
IMAGE_LAYER = {1: 3, 3: 7, 6: 11, 12: 15, 24: 19}
DEFAULT_BBOX = "-92.75,28.75,-88.25,31.25"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    vals = [float(x.strip()) for x in text.split(",")]
    if len(vals) != 4:
        raise ValueError("bbox must be west,south,east,north")
    return vals[0], vals[1], vals[2], vals[3]


def get_json(url: str, params: dict, session: requests.Session | None = None, timeout: int = 45) -> Any:
    client = session or requests
    r = client.get(url, params=params, timeout=timeout, headers={"User-Agent": "lix-flood-risk-fusion/0.1"})
    try:
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"HTTP failure for {r.url}: {e}; preview={r.text[:500]!r}") from e
    try:
        return r.json()
    except Exception as e:
        raise RuntimeError(f"Non-JSON response from {r.url}: {e}; preview={r.text[:500]!r}") from e


def sane_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        x = float(str(value).replace(",", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def pick_value(result: dict) -> tuple[float | None, str | None]:
    attrs = result.get("attributes") or {}
    for key in ["Pixel Value", "PixelValue", "pixel_value", "Raster.Value", "Value", "value", "Band_1", "Band 1"]:
        if key in attrs:
            v = sane_float(attrs.get(key))
            if v is not None:
                return v, key
    if "value" in result:
        v = sane_float(result.get("value"))
        if v is not None:
            return v, "result.value"
    for key, raw in attrs.items():
        lk = str(key).lower()
        if any(bad in lk for bad in ["objectid", "shape", "count", "red", "green", "blue", "alpha"]):
            continue
        v = sane_float(raw)
        if v is not None and 0 <= v <= 25:
            return v, str(key)
    return None, None


def cell(lon: float, lat: float, dx: float, dy: float) -> dict:
    return {"type": "Polygon", "coordinates": [[
        [lon - dx / 2, lat - dy / 2],
        [lon + dx / 2, lat - dy / 2],
        [lon + dx / 2, lat + dy / 2],
        [lon - dx / 2, lat + dy / 2],
        [lon - dx / 2, lat - dy / 2],
    ]]}


def adjusted_step(bbox: tuple[float, float, float, float], step: float, max_points: int) -> float:
    west, south, east, north = bbox
    area = max((east - west) * (north - south), 0.001)
    return max(step, math.sqrt(area / max(max_points, 1)))


def identify(session: requests.Session, image_layer_id: int, lon: float, lat: float, bbox: tuple[float, float, float, float]) -> dict:
    west, south, east, north = bbox
    return get_json(
        SERVICE_URL + "/identify",
        {
            "f": "json",
            "tolerance": "2",
            "returnGeometry": "false",
            "imageDisplay": "1000,1000,96",
            "mapExtent": json.dumps({"xmin": west, "ymin": south, "xmax": east, "ymax": north, "spatialReference": {"wkid": 4326}}),
            "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryPoint",
            "sr": "4326",
            "layers": f"all:{image_layer_id}",
        },
        session=session,
        timeout=30,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/ffg/ffg.geojson")
    ap.add_argument("--debug-json", default="docs/data/ffg_prod_arcgis_debug.json")
    ap.add_argument("--bbox", default=DEFAULT_BBOX)
    ap.add_argument("--duration", type=int, default=6, choices=[1, 3, 6, 12, 24])
    ap.add_argument("--step-deg", type=float, default=0.10)
    ap.add_argument("--max-points", type=int, default=650)
    args = ap.parse_args()

    bbox = parse_bbox(args.bbox)
    step = adjusted_step(bbox, args.step_deg, args.max_points)
    west, south, east, north = bbox
    cols = max(1, math.ceil((east - west) / step))
    rows = max(1, math.ceil((north - south) / step))
    image_layer_id = IMAGE_LAYER[args.duration]
    mosaic_layer_id = MOSAIC_LAYER[args.duration]

    debug = {
        "generated_utc": now(),
        "status": "started",
        "service_url": SERVICE_URL,
        "duration": args.duration,
        "mosaic_layer_id": mosaic_layer_id,
        "image_layer_id": image_layer_id,
        "bbox": bbox,
        "step_deg": step,
        "rows": rows,
        "cols": cols,
        "attempted_points": rows * cols,
        "value_fields_seen": {},
        "sample_errors": [],
    }

    try:
        service = get_json(SERVICE_URL, {"f": "pjson"})
        debug["service_name"] = service.get("mapName") or service.get("name")
        debug["layers"] = [{"id": l.get("id"), "name": l.get("name"), "type": l.get("type")} for l in service.get("layers", [])]
    except Exception as e:
        debug["status"] = "failed: service metadata fetch failed"
        debug["error"] = str(e)
        write_json(args.debug_json, debug)
        return 1

    session = requests.Session()
    features = []
    ffg_key = f"ffg_{args.duration}hr"

    for r in range(rows):
        lat = north - (r + 0.5) * step
        if lat < south:
            continue
        for c in range(cols):
            lon = west + (c + 0.5) * step
            if lon > east:
                continue
            try:
                resp = identify(session, image_layer_id, lon, lat, bbox)
                val = None
                field = None
                for result in resp.get("results") or []:
                    val, field = pick_value(result)
                    if val is not None:
                        break
                if val is None or val <= 0 or val > 25:
                    continue
                if field:
                    debug["value_fields_seen"][field] = debug["value_fields_seen"].get(field, 0) + 1
                props = {
                    "name": "Official RFC gridded FFG sampled cell",
                    "source": "official_rfc_gridded_ffg_arcgis_identify",
                    "official_ffg": True,
                    "source_service": SERVICE_URL,
                    "source_mosaic_layer_id": mosaic_layer_id,
                    "source_image_layer_id": image_layer_id,
                    "source_value_field": field,
                    "duration_hr": args.duration,
                    ffg_key: round(val, 3),
                    "sample_lon": round(lon, 5),
                    "sample_lat": round(lat, 5),
                    "sample_step_deg": round(step, 5),
                }
                if args.duration == 1:
                    props["ffg_1hr"] = round(val, 3)
                elif args.duration == 3:
                    props["ffg_3hr"] = round(val, 3)
                elif args.duration == 6:
                    props["ffg_6hr"] = round(val, 3)
                features.append({"type": "Feature", "properties": props, "geometry": cell(lon, lat, step, step)})
            except Exception as e:
                if len(debug["sample_errors"]) < 12:
                    debug["sample_errors"].append(str(e))

    debug["feature_count"] = len(features)
    if not features:
        debug["status"] = "failed: no usable identify values returned"
        write_json(args.debug_json, debug)
        return 1

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": now(),
            "source_service": SERVICE_URL,
            "method": "ArcGIS MapServer identify sampling of production RFC gridded FFG raster",
            "duration_hr": args.duration,
            "mosaic_layer_id": mosaic_layer_id,
            "image_layer_id": image_layer_id,
            "feature_count": len(features),
            "official_ffg": True,
        },
        "features": features,
    }
    debug["status"] = "success"
    write_json(args.out, fc)
    write_json(args.debug_json, debug)
    log(f"Wrote {args.out} with {len(features)} official FFG sampled cell(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fetch gridded Flash Flood Guidance from the NCEP/NWS ArcGIS REST MapServer.

The public NCEP IDP GIS service is the better target for this project than
static shapefiles or guessed AFOS text PILs. This script attempts to discover
and query a duration layer from the MapServer and normalize it into the
GeoJSON format used by the fusion script.

Expected output fields:
  - ffg_1hr, ffg_3hr, or ffg_6hr depending on the queried layer

Important: if the service exposes the FFG as raster-only layers that do not
support feature queries, this script will write a debug JSON and exit non-zero
without overwriting the existing FFG file.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DEFAULT_SERVICE_URL = "https://idpgis.ncep.noaa.gov/arcgis/rest/services/NWS_Forecasts_Guidance_Warnings/rfc_gridded_ffg/MapServer"
DEFAULT_BBOX = "-92.75,28.75,-88.25,31.25"
DEFAULT_LAYER_MAP = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def get_json(url: str, params: dict | None = None, timeout: int = 90) -> Any:
    headers = {"User-Agent": "lix-flood-risk-fusion/0.1"}
    r = requests.get(url, params=params, timeout=timeout, headers=headers)
    debug_url = r.url
    try:
        r.raise_for_status()
    except Exception as e:
        body = r.text[:1000] if r.text else ""
        raise RuntimeError(f"HTTP failure for {debug_url}: {e}; response preview={body!r}") from e
    try:
        return r.json()
    except Exception as e:
        body = r.text[:1000] if r.text else ""
        raise RuntimeError(f"Response from {debug_url} was not JSON: {e}; response preview={body!r}") from e


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    west, south, east, north = parts
    return west, south, east, north


def parse_layer_map(value: str) -> dict[int, int]:
    out: dict[int, int] = {}
    if not value.strip():
        return out
    for item in value.split(","):
        if not item.strip():
            continue
        dur, layer_id = item.split(":", 1)
        out[int(dur.strip())] = int(layer_id.strip())
    return out


def service_json(service_url: str) -> dict:
    return get_json(service_url.rstrip("/"), {"f": "pjson"})


def layer_json(service_url: str, layer_id: int) -> dict:
    return get_json(f"{service_url.rstrip('/')}/{layer_id}", {"f": "pjson"})


def duration_score(layer_name: str, duration: int) -> int:
    name = layer_name.lower()
    compact = re.sub(r"[^a-z0-9]+", "", name)
    score = 0
    if "ffg" in compact or "flashfloodguidance" in compact or "flashflood" in compact:
        score += 5
    patterns = [
        f"{duration}hour",
        f"{duration}hr",
        f"{duration}h",
        f"0{duration}hour" if duration < 10 else f"{duration}hour",
        f"0{duration}hr" if duration < 10 else f"{duration}hr",
        f"ffg{duration}",
        f"ffg0{duration}" if duration < 10 else f"ffg{duration}",
    ]
    for p in patterns:
        if p in compact:
            score += 10
    if re.search(rf"\b0?{duration}\s*(?:h|hr|hrs|hour|hours)\b", name):
        score += 10
    return score


def discover_layer_id(service: dict, duration: int) -> tuple[int | None, list[dict]]:
    candidates = []
    for layer in service.get("layers", []):
        name = str(layer.get("name") or "")
        score = duration_score(name, duration)
        candidates.append({"id": layer.get("id"), "name": name, "score": score, "type": layer.get("type")})
    candidates.sort(key=lambda x: x["score"], reverse=True)
    positive = [c for c in candidates if c["score"] > 0]
    if positive:
        return int(positive[0]["id"]), candidates
    return None, candidates


def query_layer_geojson(service_url: str, layer_id: int, bbox: tuple[float, float, float, float]) -> dict:
    west, south, east, north = bbox
    query_url = f"{service_url.rstrip('/')}/{layer_id}/query"
    params = {
        "f": "geojson",
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "geometry": json.dumps({
            "xmin": west,
            "ymin": south,
            "xmax": east,
            "ymax": north,
            "spatialReference": {"wkid": 4326},
        }),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outSR": "4326",
        "resultRecordCount": "5000",
        "returnExceededLimitFeatures": "true",
    }
    return get_json(query_url, params=params)


def numeric_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def candidate_value_fields(props: dict, duration: int) -> list[str]:
    keys = list(props.keys())
    preferred = []
    dur_tokens = [str(duration), f"0{duration}"] if duration < 10 else [str(duration)]
    for k in keys:
        lk = k.lower()
        compact = re.sub(r"[^a-z0-9]+", "", lk)
        if any(tok in compact for tok in [f"ffg{t}" for t in dur_tokens]):
            preferred.append(k)
        elif any(tok in compact for tok in [f"{t}hr" for t in dur_tokens] + [f"{t}hour" for t in dur_tokens]):
            preferred.append(k)
    for exact in ["ffg", "value", "gridcode", "grid_code", "pixelvalue", "pixel_value", "rastervalue", "raster_value"]:
        for k in keys:
            if k.lower() == exact:
                preferred.append(k)
    for k in keys:
        lk = k.lower()
        if any(bad in lk for bad in ["objectid", "fid", "shape", "area", "length", "id"]):
            continue
        if numeric_value(props.get(k)) is not None:
            preferred.append(k)
    seen = set()
    out = []
    for k in preferred:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def normalize_features(fc: dict, duration: int, layer_meta: dict, service_url: str) -> tuple[list[dict], str | None]:
    features = fc.get("features") or []
    if not features:
        return [], None

    sample_props = features[0].get("properties") or {}
    fields = candidate_value_fields(sample_props, duration)
    chosen_field = None
    for field in fields:
        vals = [numeric_value((f.get("properties") or {}).get(field)) for f in features[:100]]
        vals = [v for v in vals if v is not None]
        sane = [v for v in vals if 0 <= v <= 25]
        if sane:
            chosen_field = field
            break

    if not chosen_field:
        return [], None

    out = []
    ffg_key = f"ffg_{duration}hr"
    for feat in features:
        props = feat.get("properties") or {}
        val = numeric_value(props.get(chosen_field))
        if val is None or val < 0 or val > 25:
            continue
        new_props = dict(props)
        new_props[ffg_key] = round(val, 3)
        new_props["source"] = "NCEP IDP GIS ArcGIS REST"
        new_props["source_service"] = service_url
        new_props["source_layer_id"] = layer_meta.get("id")
        new_props["source_layer_name"] = layer_meta.get("name")
        new_props["source_value_field"] = chosen_field
        new_props["duration_hr"] = duration
        out.append({"type": "Feature", "properties": new_props, "geometry": feat.get("geometry")})
    return out, chosen_field


def build_debug(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "generated_utc": utc_now(),
        "status": "started",
        "service_url": args.service_url.rstrip("/"),
        "bbox_raw": args.bbox,
        "duration": args.duration,
        "attempts": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    ap.add_argument("--out", default="data/ffg/ffg.geojson")
    ap.add_argument("--debug-json", default="docs/data/ffg_arcgis_debug.json")
    ap.add_argument("--bbox", default=DEFAULT_BBOX)
    ap.add_argument("--duration", type=int, default=6, choices=[1, 3, 6])
    ap.add_argument("--layer-map", default=DEFAULT_LAYER_MAP, help="Optional duration:layer_id map, e.g. 1:0,3:1,6:2")
    args = ap.parse_args()

    debug = build_debug(args)
    debug_path = Path(args.debug_json)

    try:
        service_url = args.service_url.rstrip("/")
        bbox = parse_bbox(args.bbox)
        layer_map = parse_layer_map(args.layer_map)
        debug["bbox"] = bbox
        debug["layer_map"] = layer_map

        log(f"Reading ArcGIS service metadata: {service_url}")
        service = service_json(service_url)
        layers = service.get("layers") or []
        debug["status"] = "service metadata loaded"
        debug["service_name"] = service.get("mapName") or service.get("name")
        debug["layers"] = [{"id": l.get("id"), "name": l.get("name"), "type": l.get("type")} for l in layers]

        layer_id = layer_map.get(args.duration)
        discovery_candidates = []
        if layer_id is None:
            layer_id, discovery_candidates = discover_layer_id(service, args.duration)
        debug["discovery_candidates"] = discovery_candidates

        if layer_id is None:
            debug["status"] = "failed: no matching duration layer discovered"
            write_json(debug_path, debug)
            raise RuntimeError(f"Could not discover a {args.duration}-hour FFG layer in ArcGIS service")

        log(f"Trying ArcGIS layer {layer_id} for {args.duration}-hour FFG")
        lmeta = layer_json(service_url, layer_id)
        debug["selected_layer"] = {
            "id": layer_id,
            "name": lmeta.get("name"),
            "type": lmeta.get("type"),
            "geometryType": lmeta.get("geometryType"),
            "capabilities": lmeta.get("capabilities"),
            "fields": [{"name": f.get("name"), "type": f.get("type")} for f in lmeta.get("fields", [])],
        }

        fc = query_layer_geojson(service_url, layer_id, bbox)
        debug["queried_feature_count"] = len(fc.get("features") or [])
        features, chosen_field = normalize_features(fc, args.duration, {"id": layer_id, "name": lmeta.get("name")}, service_url)
        debug["chosen_value_field"] = chosen_field
        debug["normalized_feature_count"] = len(features)

        if not features:
            debug["status"] = "failed: no usable features or values returned"
            write_json(debug_path, debug)
            raise RuntimeError("ArcGIS FFG service query returned no usable polygon features/values. It may be raster-only or use an unexpected schema.")

        out_fc = {
            "type": "FeatureCollection",
            "metadata": {
                "generated_utc": utc_now(),
                "method": "Queried NCEP IDP GIS rfc_gridded_ffg ArcGIS REST MapServer and normalized to FFG GeoJSON",
                "source_service": service_url,
                "source_layer_id": layer_id,
                "source_layer_name": lmeta.get("name"),
                "source_value_field": chosen_field,
                "duration_hr": args.duration,
                "feature_count": len(features),
            },
            "features": features,
        }
        debug["status"] = "success"
        write_json(Path(args.out), out_fc)
        write_json(debug_path, debug)
        log(f"Wrote {args.out} with {len(features)} feature(s)")
        return 0

    except Exception as e:
        debug["generated_utc"] = utc_now()
        debug["status"] = "failed"
        debug["error"] = str(e)
        write_json(debug_path, debug)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

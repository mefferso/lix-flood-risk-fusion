#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pygrib
import requests
from matplotlib.colors import BoundaryNorm, ListedColormap
from scipy.interpolate import griddata

BASE_URL = "https://ftp-wpc.ncep.noaa.gov/workoff/ffg/"
FILE_RE = re.compile(r"5kmffg_\d{10}\.grb2")

XMIN, YMIN, XMAX, YMAX = -94.15, 28.70, -87.80, 35.20
PAN_XMIN, PAN_YMIN, PAN_XMAX, PAN_YMAX = -94.65, 28.25, -87.35, 35.65
NX, NY = 420, 430


def newest_ffg_url(base_url: str) -> tuple[str, str]:
    response = requests.get(base_url, timeout=45, headers={"User-Agent": "lix-flood-risk-fusion/0.1"})
    response.raise_for_status()
    files = sorted(set(FILE_RE.findall(response.text)))
    if not files:
        raise RuntimeError(f"No 5kmffg_YYYYMMDDHH.grb2 files found at {base_url}")
    filename = files[-1]
    return urljoin(base_url, filename), filename


def download_grib(url: str, grib_path: Path) -> None:
    grib_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=120, stream=True, headers={"User-Agent": "lix-flood-risk-fusion/0.1"}) as response:
        response.raise_for_status()
        with grib_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def intersects_local_box(lons: np.ndarray, lats: np.ndarray) -> bool:
    lon_min, lon_max = np.nanmin(lons), np.nanmax(lons)
    lat_min, lat_max = np.nanmin(lats), np.nanmax(lats)
    return not (lon_max < XMIN or lon_min > XMAX or lat_max < YMIN or lat_min > YMAX)


def message_score(grb) -> tuple[float, float, float]:
    lats, lons = grb.latlons()
    if not intersects_local_box(lons, lats):
        return (999999.0, 999999.0, 999999.0)

    lon_span = float(np.nanmax(lons) - np.nanmin(lons))
    lat_span = float(np.nanmax(lats) - np.nanmin(lats))
    area = lon_span * lat_span

    step_range = str(getattr(grb, "stepRange", ""))
    forecast_time = getattr(grb, "forecastTime", None)
    duration_penalty = 0.0 if (step_range in {"0-1", "1"} or forecast_time in {0, 1}) else 1000.0
    return (duration_penalty + area, lon_span, lat_span)


def pick_lmrfc_message(grib_path: Path):
    candidates = []
    with pygrib.open(str(grib_path)) as grbs:
        for grb in grbs:
            text = " ".join([
                str(getattr(grb, "name", "")),
                str(getattr(grb, "shortName", "")),
                str(getattr(grb, "parameterName", "")),
                str(getattr(grb, "typeOfLevel", "")),
            ]).lower()
            if "ffg" not in text and "flash flood guidance" not in text:
                if "precip" not in text and "rain" not in text:
                    continue
            try:
                score = message_score(grb)
            except Exception:
                continue
            if score[0] < 999999.0:
                candidates.append((score, grb.messagenumber))

    if not candidates:
        raise RuntimeError("No usable FFG-like GRIB2 message intersected the LA/MS domain.")

    candidates.sort(key=lambda item: item[0])
    grbs = pygrib.open(str(grib_path))
    return grbs, grbs.message(candidates[0][1])


def values_to_inches(values: np.ndarray, units: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    arr[arr > 10000] = np.nan
    arr[arr <= 0] = np.nan
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    units_lower = (units or "").lower()
    median_value = float(np.nanmedian(finite))
    if "kg" in units_lower or "mm" in units_lower or median_value > 20:
        arr = arr / 25.4
    elif units_lower in {"m", "meter", "meters"}:
        arr = arr * 39.3701
    arr[(arr <= 0) | (arr > 20)] = np.nan
    return arr


def build_grid(base_url: str, grib_path: Path) -> dict:
    url, filename = newest_ffg_url(base_url)
    print(f"Downloading {url}", flush=True)
    download_grib(url, grib_path)

    grbs, grb = pick_lmrfc_message(grib_path)
    try:
        lats, lons = grb.latlons()
        values = np.ma.filled(grb.values, np.nan)
        values_in = values_to_inches(values, getattr(grb, "units", ""))

        mask = (
            np.isfinite(lats) & np.isfinite(lons) & np.isfinite(values_in) &
            (lons >= PAN_XMIN) & (lons <= PAN_XMAX) &
            (lats >= PAN_YMIN) & (lats <= PAN_YMAX)
        )
        if np.count_nonzero(mask) < 10:
            raise RuntimeError("Selected GRIB message had too few valid local points.")

        grid_lon = np.linspace(XMIN, XMAX, NX)
        grid_lat = np.linspace(YMIN, YMAX, NY)
        gx, gy = np.meshgrid(grid_lon, grid_lat)
        points = np.column_stack([lons[mask].ravel(), lats[mask].ravel()])
        vals = values_in[mask].ravel()
        local_grid = griddata(points, vals, (gx, gy), method="nearest")
        local_grid[(local_grid <= 0) | (local_grid > 20)] = np.nan

        metadata = {
            "source_url": url,
            "source_file": filename,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
            "method": "Native WPC/LMRFC GRIB2 FFG locally regridded for LA/MS",
            "duration_hr": 1,
            "official_ffg": True,
            "grib_name": str(getattr(grb, "name", "")),
            "grib_short_name": str(getattr(grb, "shortName", "")),
            "grib_units": str(getattr(grb, "units", "")),
            "grib_data_date": str(getattr(grb, "dataDate", "")),
            "grib_data_time": str(getattr(grb, "dataTime", "")),
            "grib_forecast_time": str(getattr(grb, "forecastTime", "")),
            "grib_step_range": str(getattr(grb, "stepRange", "")),
            "grib_message_number": int(getattr(grb, "messagenumber", -1)),
        }
    finally:
        grbs.close()

    return {
        "extent": {"xmin": XMIN, "ymin": YMIN, "xmax": XMAX, "ymax": YMAX},
        "panLimit": {"xmin": PAN_XMIN, "ymin": PAN_YMIN, "xmax": PAN_XMAX, "ymax": PAN_YMAX},
        "nx": NX,
        "ny": NY,
        "metadata": metadata,
        "values": [[None if not np.isfinite(v) else round(float(v), 2) for v in row] for row in local_grid],
    }


def write_overlay(grid: dict, png_path: Path) -> None:
    arr = np.array([[np.nan if v is None else float(v) for v in row] for row in grid["values"]])
    colors = ["#0033cc", "#0066ff", "#00a6ff", "#00d0d0", "#00b050", "#80d000", "#ffd000", "#ff9900", "#ff0000", "#cc00cc"]
    bounds = [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 20]
    cmap = ListedColormap(colors)
    cmap.set_bad((0, 0, 0, 0))
    norm = BoundaryNorm(bounds, cmap.N)
    fig = plt.figure(figsize=(10, 10), dpi=160)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(arr, origin="lower", extent=[XMIN, XMAX, YMIN, YMAX], cmap=cmap, norm=norm, interpolation="nearest")
    ax.set_xlim(XMIN, XMAX)
    ax.set_ylim(YMIN, YMAX)
    ax.axis("off")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def grid_to_geojson(grid: dict, out_path: Path, step: int = 6) -> None:
    arr = grid["values"]
    e = grid["extent"]
    nx = int(grid["nx"])
    ny = int(grid["ny"])
    dx = (e["xmax"] - e["xmin"]) / (nx - 1)
    dy = (e["ymax"] - e["ymin"]) / (ny - 1)
    features = []
    for row in range(0, ny, step):
        lat = e["ymin"] + row * dy
        for col in range(0, nx, step):
            v = arr[row][col]
            if v is None:
                continue
            lon = e["xmin"] + col * dx
            poly = [[
                [lon - dx * step / 2, lat - dy * step / 2],
                [lon + dx * step / 2, lat - dy * step / 2],
                [lon + dx * step / 2, lat + dy * step / 2],
                [lon - dx * step / 2, lat + dy * step / 2],
                [lon - dx * step / 2, lat - dy * step / 2],
            ]]
            features.append({
                "type": "Feature",
                "properties": {
                    "name": "Native LMRFC 1-hour FFG sampled cell",
                    "source": "native_wpc_lmrfc_grib2",
                    "official_ffg": True,
                    "duration_hr": 1,
                    "ffg_1hr": float(v),
                    "ffg_in": float(v),
                    "sample_lon": round(lon, 5),
                    "sample_lat": round(lat, 5),
                },
                "geometry": {"type": "Polygon", "coordinates": poly},
            })
    fc = {"type": "FeatureCollection", "metadata": {**grid.get("metadata", {}), "feature_count": len(features)}, "features": features}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fc, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--grib", default="data/ffg/latest_lmrfc_ffg.grb2")
    ap.add_argument("--grid-json", default="docs/data/ffg_grid.json")
    ap.add_argument("--overlay-png", default="docs/data/ffg_overlay.png")
    ap.add_argument("--geojson", default="data/ffg/ffg.geojson")
    ap.add_argument("--geojson-step", type=int, default=6)
    args = ap.parse_args()

    grid = build_grid(args.base_url, Path(args.grib))
    Path(args.grid_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.grid_json).write_text(json.dumps(grid, separators=(",", ":")), encoding="utf-8")
    write_overlay(grid, Path(args.overlay_png))
    grid_to_geojson(grid, Path(args.geojson), step=args.geojson_step)
    print(f"Created {args.grid_json}, {args.overlay_png}, and {args.geojson}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

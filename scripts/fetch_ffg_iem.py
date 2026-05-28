#!/usr/bin/env python3
"""Fetch real IEM Autoplot Flash Flood Guidance PNGs for display.

This downloads IEM Autoplot #178 output. It is intended for the web/display
layer only. Numeric risk fusion should continue to use data/ffg/ffg.geojson
or another normalized gridded/polygon FFG source.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests


IEM_AUTOPLOT_URL = "https://mesonet.agron.iastate.edu/plotting/auto/"


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def parse_ts(ts: str | None) -> datetime:
    if not ts:
        now = datetime.now(timezone.utc)
        return now.replace(minute=0, second=0, microsecond=0)

    cleaned = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        dt = datetime.strptime(ts.strip(), "%Y/%m/%d %H%M").replace(tzinfo=timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iem_ts(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return f"{dt:%Y/%m/%d %H%M}"


def build_url(valid_dt: datetime, wfo: str, hour: int, cmap: str, dpi: int) -> str:
    params = {
        "_wait": "no",
        "q": "178",
        "t": "cwa",
        "wfo": wfo.upper(),
        "state": "IA",
        "hour": str(hour),
        "ilabel": "yes",
        "ts": iem_ts(valid_dt),
        "cmap": cmap,
        "_r": "t",
        "dpi": str(dpi),
        "_fmt": "png",
    }
    return f"{IEM_AUTOPLOT_URL}?{urlencode(params)}"


def fetch_png(url: str, out: Path) -> None:
    headers = {"User-Agent": "lix-flood-risk-fusion/0.2"}
    r = requests.get(url, timeout=120, headers=headers)
    r.raise_for_status()
    content_type = r.headers.get("content-type", "")
    if "image" not in content_type and not r.content.startswith(b"\x89PNG"):
        raise RuntimeError(f"IEM did not return a PNG. content-type={content_type!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wfo", default="LIX")
    ap.add_argument("--hours", default="1,3,6", help="Comma-separated FFG durations to fetch")
    ap.add_argument("--ts", default=None, help="UTC valid time, ISO or 'YYYY/MM/DD HHMM'. Defaults to current UTC hour.")
    ap.add_argument("--cmap", default="gist_rainbow")
    ap.add_argument("--dpi", type=int, default=100)
    ap.add_argument("--out-dir", default="docs/assets/ffg")
    ap.add_argument("--manifest-out", default="docs/data/ffg_manifest.json")
    args = ap.parse_args()

    valid_dt = parse_ts(args.ts)
    out_dir = Path(args.out_dir)

    items = []
    for hour_txt in args.hours.split(","):
        hour = int(hour_txt.strip())
        if hour not in (1, 3, 6):
            raise RuntimeError("FFG hour must be one of 1, 3, or 6")
        url = build_url(valid_dt, args.wfo, hour, args.cmap, args.dpi)
        out = out_dir / f"iem_ffg_{hour}hr.png"
        log(f"Fetching {hour}-hour IEM FFG: {url}")
        fetch_png(url, out)
        items.append({
            "hour": hour,
            "path": str(out).replace("docs/", ""),
            "url": url,
        })
        log(f"Wrote {out}")

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "valid_time_utc": valid_dt.isoformat(),
        "source": "IEM Autoplot #178 NWS RFC Flash Flood Guidance Plots",
        "note": "PNG is for display only; numeric fusion uses normalized FFG data.",
        "wfo": args.wfo.upper(),
        "items": items,
    }
    manifest_path = Path(args.manifest_out)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"Wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise

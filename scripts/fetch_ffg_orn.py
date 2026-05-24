#!/usr/bin/env python3
"""Fetch and normalize LMRFC county/parish Flash Flood Guidance.

This is intentionally conservative:
- Try the NWS API first for the latest FFG product from location ORN.
- Fall back to the IEM AFOS text-product endpoint.
- Parse county/parish rows with 1/3/6 hour FFG values.
- Join parsed rows to county/parish polygons from a public county GeoJSON.

If parsing fails, the script exits non-zero and does not overwrite the existing
FFG file. The workflow can then keep using the existing placeholder/sample file.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

DEFAULT_PRODUCT_TYPE = "FFG"
DEFAULT_LOCATION = "ORN"
DEFAULT_PIL = "FFGORN"
DEFAULT_COUNTIES_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
STATE_FIPS = {"LA": "22", "MS": "28", "AL": "01"}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def get_json(url: str, timeout: int = 60) -> Any:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "lix-flood-risk-fusion/0.1"})
    r.raise_for_status()
    return r.json()


def get_text(url: str, timeout: int = 60) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "lix-flood-risk-fusion/0.1"})
    r.raise_for_status()
    return r.text


def fetch_from_weather_api(product_type: str, location: str) -> tuple[str, dict]:
    listing_url = f"https://api.weather.gov/products/types/{product_type}/locations/{location}"
    listing = get_json(listing_url)
    graph = listing.get("@graph") or []
    if not graph:
        raise RuntimeError(f"NWS API returned no {product_type}/{location} products")

    product = graph[0]
    product_url = product.get("@id")
    if not product_url:
        product_id = product.get("id")
        if not product_id:
            raise RuntimeError("NWS API product listing did not include @id or id")
        product_url = f"https://api.weather.gov/products/{product_id}"

    data = get_json(product_url)
    text = data.get("productText") or data.get("text") or ""
    if not text.strip():
        raise RuntimeError("NWS API product did not include productText")

    meta = {
        "source": "api.weather.gov",
        "product_url": product_url,
        "product_type": product_type,
        "location": location,
        "issue_time": data.get("issuanceTime") or product.get("issuanceTime"),
    }
    return text, meta


def strip_basic_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", "", text)
    text = re.sub(r"(?is)<style.*?</style>", "", text)
    pre = re.search(r"(?is)<pre[^>]*>(.*?)</pre>", text)
    if pre:
        text = pre.group(1)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    return html.unescape(text)


def fetch_from_iem(pil: str) -> tuple[str, dict]:
    url = f"https://mesonet.agron.iastate.edu/wx/afos/p.php?pil={pil}"
    text = strip_basic_html(get_text(url))
    if not text.strip():
        raise RuntimeError("IEM AFOS endpoint returned empty text")
    meta = {"source": "IEM AFOS", "product_url": url, "pil": pil}
    return text, meta


def fetch_ffg_text(product_type: str, location: str, pil: str) -> tuple[str, dict]:
    errors = []
    try:
        log(f"Fetching latest {product_type}/{location} from api.weather.gov")
        return fetch_from_weather_api(product_type, location)
    except Exception as e:
        errors.append(f"NWS API: {e}")
        log(f"NWS API fetch failed: {e}")

    try:
        log(f"Fetching latest {pil} from IEM AFOS")
        return fetch_from_iem(pil)
    except Exception as e:
        errors.append(f"IEM: {e}")
        log(f"IEM fetch failed: {e}")

    raise RuntimeError("Could not fetch FFG text product. " + " | ".join(errors))


def norm_name(value: str) -> str:
    s = value.upper()
    s = s.replace(".", " ")
    s = s.replace("'", "")
    s = re.sub(r"\b(PARISH|COUNTY|CNTY|CO)\b", "", s)
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = re.sub(r"\bST\s+", "ST ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def cleanup_area_name(raw: str) -> str:
    s = raw.upper()
    # Remove common product/table cruft and any leading zone/county codes.
    s = re.sub(r"\b[A-Z]{2}[CZ]\d{3}(?:[-,]\d{3})*\b", " ", s)
    s = re.sub(r"\b[A-Z]{2}\d{3}(?:[-,]\d{3})*\b", " ", s)
    s = re.sub(r"\bCOUNTY\b|\bPARISH\b", " ", s)
    s = re.sub(r"[^A-Z0-9 .'-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_float_token(token: str) -> float | None:
    t = token.strip().upper()
    if t in {"M", "MM", "NA", "--"}:
        return None
    try:
        return float(t)
    except Exception:
        return None


def parse_ffg_rows(product_text: str) -> list[dict]:
    rows: list[dict] = []
    float_re = re.compile(r"(?:\d+\.\d+|\d+)")

    for raw_line in product_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        upper = line.upper()

        if any(skip in upper for skip in ["FLASH FLOOD GUIDANCE", "NATIONAL WEATHER", "INCHES", "VALID", "ISSUED", "COUNTY", "PARISH"]):
            # Header lines sometimes include the word county/parish; still allow them
            # through below only if they contain a clear name plus at least 3 values.
            pass

        matches = list(float_re.finditer(line))
        if len(matches) < 3:
            continue

        # Use the final 3+ numeric tokens as guidance values. Product tables often
        # include exactly 1/3/6hr, and some include 12/24hr after those.
        vals = [parse_float_token(m.group(0)) for m in matches]
        vals = [v for v in vals if v is not None]
        if len(vals) < 3:
            continue

        first_num = matches[0].start()
        area_raw = cleanup_area_name(line[:first_num])
        area_norm = norm_name(area_raw)

        # Avoid parsing headers or station/product codes as fake counties.
        if not area_norm or len(area_norm) < 3:
            continue
        if area_norm in {"HR", "HOUR", "HOURS", "BASIN", "AREA", "STATE", "COUNTY", "PARISH"}:
            continue
        if area_norm.startswith(("FOUS", "FFG", "TTAA", "\"")):
            continue

        # Filter out rows where the values are obviously not FFG inches.
        if vals[0] > 15 or vals[1] > 20 or vals[2] > 25:
            continue

        rows.append({
            "area_raw": area_raw,
            "area_norm": area_norm,
            "ffg_1hr": vals[0],
            "ffg_3hr": vals[1],
            "ffg_6hr": vals[2],
            "values": vals,
            "line": line.strip(),
        })

    # Deduplicate by normalized area name. Keep the lowest 6hr value if repeated.
    dedup: dict[str, dict] = {}
    for row in rows:
        key = row["area_norm"]
        if key not in dedup or row["ffg_6hr"] < dedup[key]["ffg_6hr"]:
            dedup[key] = row
    return list(dedup.values())


def load_counties(url: str, states: list[str]) -> list[dict]:
    fc = get_json(url, timeout=90)
    wanted_fips = {STATE_FIPS[s] for s in states if s in STATE_FIPS}
    out = []
    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        fips = str(feat.get("id") or props.get("GEOID") or props.get("GEO_ID") or "")
        if len(fips) >= 5:
            state_fips = fips[:2]
        else:
            state_fips = str(props.get("STATE") or "")
        if state_fips not in wanted_fips:
            continue
        name = str(props.get("NAME") or "")
        if not name:
            continue
        props["name_norm"] = norm_name(name)
        props["state_fips"] = state_fips
        feat["properties"] = props
        out.append(feat)
    return out


def join_rows_to_counties(rows: list[dict], counties: list[dict], meta: dict) -> tuple[list[dict], list[dict]]:
    by_name: dict[str, list[dict]] = {}
    for feat in counties:
        key = feat.get("properties", {}).get("name_norm")
        if key:
            by_name.setdefault(key, []).append(feat)

    features = []
    unmatched = []
    for row in rows:
        matches = by_name.get(row["area_norm"], [])
        if not matches:
            unmatched.append(row)
            continue
        for feat in matches:
            props = dict(feat.get("properties") or {})
            props.update({
                "name": props.get("NAME") or row["area_raw"].title(),
                "ffg_1hr": row["ffg_1hr"],
                "ffg_3hr": row["ffg_3hr"],
                "ffg_6hr": row["ffg_6hr"],
                "source": meta.get("source"),
                "source_product": meta.get("pil") or meta.get("product_type"),
                "source_url": meta.get("product_url"),
                "issue_time": meta.get("issue_time"),
                "raw_ffg_line": row["line"],
            })
            features.append({"type": "Feature", "properties": props, "geometry": feat.get("geometry")})
    return features, unmatched


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/ffg/ffg.geojson")
    ap.add_argument("--product-type", default=DEFAULT_PRODUCT_TYPE)
    ap.add_argument("--location", default=DEFAULT_LOCATION)
    ap.add_argument("--pil", default=DEFAULT_PIL)
    ap.add_argument("--counties-url", default=DEFAULT_COUNTIES_URL)
    ap.add_argument("--states", default="LA,MS")
    ap.add_argument("--raw-out", default="docs/data/latest_ffg_product.txt")
    ap.add_argument("--debug-json", default="docs/data/ffg_parse_debug.json")
    args = ap.parse_args()

    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    text, meta = fetch_ffg_text(args.product_type, args.location, args.pil)

    raw_path = Path(args.raw_out)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(text, encoding="utf-8")
    log(f"Wrote raw FFG product text to {raw_path}")

    rows = parse_ffg_rows(text)
    log(f"Parsed {len(rows)} candidate FFG row(s)")
    if not rows:
        raise RuntimeError("Parsed zero FFG rows; leaving existing FFG GeoJSON untouched")

    counties = load_counties(args.counties_url, states)
    log(f"Loaded {len(counties)} county/parish polygon(s) for {','.join(states)}")
    features, unmatched = join_rows_to_counties(rows, counties, meta)
    log(f"Matched {len(features)} county/parish polygon feature(s); unmatched rows: {len(unmatched)}")

    debug = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "parsed_rows": rows,
        "unmatched_rows": unmatched,
        "matched_feature_count": len(features),
    }
    write_json(Path(args.debug_json), debug)

    if not features:
        raise RuntimeError("Matched zero FFG rows to county/parish polygons; leaving existing FFG GeoJSON untouched")

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "method": "Parsed county/parish Flash Flood Guidance text product and joined to county polygons",
            "source": meta,
            "states": states,
            "feature_count": len(features),
            "unmatched_count": len(unmatched),
        },
        "features": features,
    }
    write_json(Path(args.out), fc)
    log(f"Wrote {args.out} with {len(features)} feature(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise

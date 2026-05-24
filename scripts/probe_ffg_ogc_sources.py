#!/usr/bin/env python3
"""Probe official NWS CloudGIS/OGC sources for RFC gridded FFG.

This does not feed the fusion pipeline yet. It is a source-discovery/debug tool
that runs in GitHub Actions and writes a JSON report we can inspect.

Targets:
- The LMRFC-linked NWS GIS metadata XML for rfc_gridded_ffg.
- Likely OpenGeo/GeoServer WMS/WCS/WFS GetCapabilities endpoints.

Goal:
- Find a real machine-readable layer/coverage name for gridded FFG.
- Identify whether it is exposed as WCS coverage, WMS layer, or WFS features.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

METADATA_URL = "https://www.weather.gov/source/gis/Geodata/rfc_gridded_ffg.xml"

CANDIDATE_OWS_BASES = [
    # Global GeoServer OWS endpoint.
    "https://opengeo.ncep.noaa.gov/geoserver/ows",

    # Likely workspace/layer patterns based on NWS OpenGeo directory examples.
    "https://opengeo.ncep.noaa.gov/geoserver/rfc/rfc_gridded_ffg/ows",
    "https://opengeo.ncep.noaa.gov/geoserver/rfc_gridded_ffg/rfc_gridded_ffg/ows",
    "https://opengeo.ncep.noaa.gov/geoserver/water/rfc_gridded_ffg/ows",
    "https://opengeo.ncep.noaa.gov/geoserver/hydro/rfc_gridded_ffg/ows",
    "https://opengeo.ncep.noaa.gov/geoserver/nws/rfc_gridded_ffg/ows",
    "https://opengeo.ncep.noaa.gov/geoserver/ffg/rfc_gridded_ffg/ows",
    "https://opengeo.ncep.noaa.gov/geoserver/NWS_Forecasts_Guidance_Warnings/rfc_gridded_ffg/ows",

    # Older/alternate mapservices style, in case docs point there.
    "https://mapservices.weather.noaa.gov/geoserver/ows",
    "https://mapservices.weather.noaa.gov/geoserver/rfc/rfc_gridded_ffg/ows",
]

SERVICE_QUERIES = [
    ("WCS", "2.0.1", {"service": "WCS", "version": "2.0.1", "request": "GetCapabilities"}),
    ("WCS", "1.0.0", {"service": "WCS", "version": "1.0.0", "request": "GetCapabilities"}),
    ("WMS", "1.3.0", {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"}),
    ("WMS", "1.1.1", {"service": "WMS", "version": "1.1.1", "request": "GetCapabilities"}),
    ("WFS", "2.0.0", {"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"}),
]

KEYWORDS = ["ffg", "flash", "flood", "guidance", "rfc"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def get_text(url: str, params: dict | None = None, timeout: int = 45) -> tuple[str, str, str]:
    headers = {"User-Agent": "lix-flood-risk-fusion/0.1"}
    r = requests.get(url, params=params, timeout=timeout, headers=headers)
    final_url = r.url
    content_type = r.headers.get("content-type", "")
    r.raise_for_status()
    return r.text, final_url, content_type


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def text_of(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def keyword_hit(value: str) -> bool:
    v = value.lower()
    return any(k in v for k in KEYWORDS)


def parse_metadata_links(xml_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"links": [], "text_matches": []}
    for match in re.finditer(r"https?://[^\s\"'<>]+", xml_text):
        url = match.group(0).rstrip(".,);]")
        if url not in out["links"]:
            out["links"].append(url)
    for line in xml_text.splitlines():
        if keyword_hit(line):
            out["text_matches"].append(line.strip()[:500])
    return out


def parse_capabilities(xml_text: str, service: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "parse_status": "ok",
        "matching_items": [],
        "all_items_sample": [],
        "service_links": [],
        "formats": [],
    }
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except Exception as e:
        info["parse_status"] = f"xml parse failed: {e}"
        info["preview"] = xml_text[:1000]
        return info

    # Capture advertised operation URLs and output formats.
    for elem in root.iter():
        lname = local_name(elem.tag)
        if lname.lower() in {"get", "post"}:
            href = elem.attrib.get("{http://www.w3.org/1999/xlink}href") or elem.attrib.get("href")
            if href and href not in info["service_links"]:
                info["service_links"].append(href)
        if lname.lower() in {"format", "supportedformat"}:
            val = text_of(elem)
            if val and val not in info["formats"]:
                info["formats"].append(val)

    items = []
    if service == "WMS":
        # WMS Layer elements commonly contain Name and Title children.
        for elem in root.iter():
            if local_name(elem.tag) != "Layer":
                continue
            name = ""
            title = ""
            for child in elem:
                if local_name(child.tag) == "Name" and not name:
                    name = text_of(child)
                elif local_name(child.tag) == "Title" and not title:
                    title = text_of(child)
            if name or title:
                items.append({"name": name, "title": title})
    elif service == "WCS":
        for elem in root.iter():
            lname = local_name(elem.tag)
            if lname not in {"CoverageSummary", "CoverageOfferingBrief"}:
                continue
            item: dict[str, str] = {}
            for child in elem.iter():
                cname = local_name(child.tag)
                if cname in {"CoverageId", "Identifier", "Name"} and "name" not in item:
                    item["name"] = text_of(child)
                elif cname == "Title" and "title" not in item:
                    item["title"] = text_of(child)
            if item.get("name") or item.get("title"):
                items.append(item)
    else:  # WFS
        for elem in root.iter():
            if local_name(elem.tag) != "FeatureType":
                continue
            item = {}
            for child in elem:
                cname = local_name(child.tag)
                if cname == "Name":
                    item["name"] = text_of(child)
                elif cname == "Title":
                    item["title"] = text_of(child)
            if item.get("name") or item.get("title"):
                items.append(item)

    info["item_count"] = len(items)
    info["all_items_sample"] = items[:40]
    info["matching_items"] = [x for x in items if keyword_hit((x.get("name", "") + " " + x.get("title", "")))][:80]
    return info


def candidate_bases_from_metadata(metadata: dict[str, Any]) -> list[str]:
    bases = []
    for url in metadata.get("links", []):
        if "GetCapabilities" in url or "service=" in url.lower() or "/ows" in url:
            # Strip query to leave OWS base where possible.
            base = url.split("?", 1)[0].rstrip("/")
            if base and base not in bases:
                bases.append(base)
        elif "opengeo" in url and "/geoserver/" in url:
            base = url.split("?", 1)[0].rstrip("/")
            if base and base not in bases:
                bases.append(base)
    return bases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata-url", default=METADATA_URL)
    ap.add_argument("--out", default="docs/data/ffg_ogc_probe.json")
    args = ap.parse_args()

    debug: dict[str, Any] = {
        "generated_utc": utc_now(),
        "status": "started",
        "metadata_url": args.metadata_url,
        "metadata": None,
        "candidate_bases": [],
        "attempts": [],
    }

    try:
        log(f"Fetching FFG metadata XML: {args.metadata_url}")
        txt, final_url, ctype = get_text(args.metadata_url)
        metadata = parse_metadata_links(txt)
        metadata["final_url"] = final_url
        metadata["content_type"] = ctype
        metadata["byte_count"] = len(txt.encode("utf-8"))
        debug["metadata"] = metadata
    except Exception as e:
        debug["metadata"] = {"status": "failed", "error": str(e)}

    bases = []
    if isinstance(debug.get("metadata"), dict):
        bases.extend(candidate_bases_from_metadata(debug["metadata"]))
    for base in CANDIDATE_OWS_BASES:
        if base not in bases:
            bases.append(base)
    debug["candidate_bases"] = bases

    found = []
    for base in bases:
        for service, version, params in SERVICE_QUERIES:
            attempt: dict[str, Any] = {"base_url": base, "service": service, "version": version, "started_utc": utc_now()}
            try:
                txt, final_url, ctype = get_text(base, params=params)
                attempt.update({
                    "status": "fetched",
                    "final_url": final_url,
                    "content_type": ctype,
                    "byte_count": len(txt.encode("utf-8")),
                })
                cap = parse_capabilities(txt, service)
                attempt.update(cap)
                if cap.get("matching_items"):
                    attempt["status"] = "candidate_match"
                    found.append({"base_url": base, "service": service, "version": version, "matching_items": cap.get("matching_items")})
            except Exception as e:
                attempt.update({"status": "failed", "error": str(e)})
            debug["attempts"].append(attempt)
            # Keep the file useful during long/debug runs.
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(debug, indent=2), encoding="utf-8")

    debug["generated_utc"] = utc_now()
    debug["status"] = "found candidates" if found else "no candidates found"
    debug["found_candidates"] = found
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(debug, indent=2), encoding="utf-8")
    log(f"Wrote {args.out}; status={debug['status']}; candidate count={len(found)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

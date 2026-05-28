[README.md](https://github.com/user-attachments/files/28194469/README.md)
# LIX Flood Risk Fusion Starter

This is a starter repo scaffold for a **forecast-based flash flood risk fusion map**.

The basic idea:

> highlight areas where forecast 6-hour HREF ensemble max QPF exceeds real-time FFG, especially where local history says the area is flood-prone.

This is intentionally built in pieces so it can work before every final data feed is perfect.

## What it fuses

1. **Forecast rainfall**
   - Pulls the latest HREF 6-hour ensemble max QPF value grid from your existing `href-qpf-viewer` GitHub Pages output.
   - Default catalog:
     `https://mefferso.github.io/href-qpf-viewer/data/catalog.json`

2. **Flash Flood Guidance**
   - Numeric fusion uses `data/ffg/ffg.geojson`.
   - This can be county/parish based, basin based, or gridded-to-polygons.
   - Required fields:
     - `ffg_1hr`
     - `ffg_3hr`
     - `ffg_6hr`

3. **Real IEM RFC FFG display maps**
   - The workflow can now fetch actual IEM Autoplot #178 RFC FFG maps.
   - These are for display only.
   - PNGs are written to:

```text
docs/assets/ffg/
```

   Example:

```bash
python scripts/fetch_ffg_iem.py --wfo LIX --hours 1,3,6
```

4. **Local flood-prone areas**
   - Starts with `data/flood_prone/flood_prone.geojson`.
   - This can come from your Flash-Flood-Guidance / Flash-Flood-Guidance-Map / Urban-Flash-Flooding outputs.
   - For now, any polygon in this file is treated as locally susceptible.

## Output

The workflow writes:

```text
docs/data/flash_flood_risk.geojson
docs/data/flash_flood_risk_raw_cells.geojson
docs/data/manifest.json
docs/data/ffg_manifest.json
```

and serves:

```text
docs/index.html
```

## Risk logic, v0.2

For each HREF grid cell:

```text
qpf_ratio = href_qpf / ffg
```

The workflow supports 1-hour, 3-hour, and 6-hour FFG fusion.

Then:

| Category | Logic |
|---|---|
| Significant | ratio >= 1.50 and flood-prone |
| Probable | ratio >= 1.00 and flood-prone |
| Possible | ratio >= 0.75 and flood-prone |

This is deliberately simple. No magic AI fairy dust. Just a clean first fusion pass.

## Important architecture note

The IEM PNGs are NOT used for math.

The architecture is intentionally:

```text
Numeric FFG data → risk fusion math
IEM PNGs         → operational display layer
```

That avoids trying to reverse-engineer FFG values from rendered image colors like a feral raccoon doing computer vision.

## First local test

```bash
python -m pip install -r requirements.txt
python scripts/make_sample_inputs.py

# Fetch real IEM FFG display maps
python scripts/fetch_ffg_iem.py --wfo LIX --hours 1,3,6

# Run fusion
python scripts/fuse_flash_flood_risk.py \
  --href-catalog-url https://mefferso.github.io/href-qpf-viewer/data/catalog.json \
  --ffg data/ffg/ffg.geojson \
  --flood-prone data/flood_prone/flood_prone.geojson \
  --duration 6
```

Then open:

```text
docs/index.html
```

## Near-term upgrade path

1. Replace sample FFG polygons with real gridded RFC FFG normalization.
2. Swap the placeholder flood-prone polygons for your real heatmap/threshold polygons.
3. Add multiple durations:
   - 1-hour HREF/HRRR vs 1-hour FFG
   - 3-hour HREF/HRRR vs 3-hour FFG
   - 6-hour HREF vs 6-hour FFG
4. Add observed MRMS QPE exceedance for nowcasting:
   - observed 1/3/6 hour QPE vs FFG
5. Add confidence layer:
   - HREF probability of exceeding FFG
   - neighborhood of max QPF
   - PWAT / Corfidi / storm motion flags later
6. Add native gridded FFG ingestion:
   - GRIB2 ingest
   - xarray/cfgrib support
   - direct QPF/FFG raster fusion

## Repo layout

```text
.
├── .github/workflows/build.yml
├── data/
│   ├── ffg/
│   │   └── ffg.geojson
│   └── flood_prone/
│       └── flood_prone.geojson
├── docs/
│   ├── assets/
│   │   └── ffg/
│   ├── index.html
│   └── data/
├── scripts/
│   ├── fetch_ffg_iem.py
│   ├── fuse_flash_flood_risk.py
│   ├── make_sample_inputs.py
│   └── normalize_ffg_geojson.py
└── requirements.txt
```
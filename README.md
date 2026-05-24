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
   - Starts with `data/ffg/ffg.geojson`.
   - This can be county/parish based, basin based, or gridded-to-polygons.
   - Required field, initially:
     - `ffg_6hr`

3. **Local flood-prone areas**
   - Starts with `data/flood_prone/flood_prone.geojson`.
   - This can come from your Flash-Flood-Guidance / Flash-Flood-Guidance-Map / Urban-Flash-Flooding outputs.
   - For now, any polygon in this file is treated as locally susceptible.

## Output

The workflow writes:

```text
docs/data/flash_flood_risk.geojson
docs/data/flash_flood_risk_raw_cells.geojson
docs/data/manifest.json
```

and serves:

```text
docs/index.html
```

## Risk logic, v0.1

For each HREF grid cell:

```text
qpf_ratio = href_6hr_qpf / ffg_6hr
```

Then:

| Category | Logic |
|---|---|
| Significant | ratio >= 1.50 and flood-prone |
| Probable | ratio >= 1.00 and flood-prone |
| Possible | ratio >= 0.75 and flood-prone |

This is deliberately simple. No magic AI fairy dust. Just a clean first fusion pass.

## First local test

```bash
python -m pip install -r requirements.txt
python scripts/make_sample_inputs.py
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

## GitHub Pages setup

1. Create a new public repo, suggested name:
   `lix-flood-risk-fusion`
2. Upload this folder.
3. Go to **Settings → Pages**.
4. Set source to **GitHub Actions**.
5. Run the workflow manually.

## The hard part still to solve

The most important unresolved piece is the **best automated real-time FFG feed**.

This repo assumes we normalize whatever source we choose into:

```text
data/ffg/ffg.geojson
```

with fields like:

```json
{
  "ffg_1hr": 2.1,
  "ffg_3hr": 3.0,
  "ffg_6hr": 4.2,
  "source": "RFC/FFG/feed name",
  "valid_time_utc": "2026-05-24T12:00:00Z"
}
```

Once that exists, the fusion logic is plug-and-play.

## Near-term upgrade path

1. Get sample basin/county/parish FFG into `data/ffg/ffg.geojson`.
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
│   ├── index.html
│   └── data/
├── scripts/
│   ├── fuse_flash_flood_risk.py
│   ├── make_sample_inputs.py
│   └── normalize_ffg_geojson.py
└── requirements.txt
```

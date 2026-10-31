# Barrierefrei: step-free route agent

Plans walking routes without stairs, high kerbs or broken elevators by reading Mapillary street
photos with SAM 3.1 and Muse Spark, fusing them with OpenStreetMap and live Deutsche Bahn
elevator status. Built for the Meta Global AI Developer Hackathon (Oct 27 - Nov 4, 2026).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in the four keys
```

### Keys you need

| Key | Where to get it |
|---|---|
| `MODEL_API_KEY` | dev.meta.ai dashboard, create an API key (hackathon credits) |
| `MAPILLARY_TOKEN` | mapillary.com, log in, Dashboard, Developers, register an app, copy the **Access Token** (`MLY|...`); ignore the client secret |
| `DB_CLIENT_ID`, `DB_API_KEY` | developers.deutschebahn.com, register, create an application, subscribe it to **FaSta - Station Facilities Status** (free plan) |

## Hello-world checks (run from the repo root)

```bash
python hello/01_mapillary.py                       # photos in the demo area, copy one URL
python hello/02_sam.py "<photo url>" "curb"        # SAM 3.1 boxes
python hello/03_spark_vision.py "<photo url>"      # Muse Spark accessibility JSON
python hello/04_voice.py request.wav               # Muse Voice Transcribe
python hello/05_fasta.py                           # Darmstadt Hbf elevator status
```

Each script prints a real result when its key works.

## Pipeline

| Step | Command | What it does |
|---|---|---|
| 1. Ingest | `python -m barrierfrei.ingest` | Downloads the OpenStreetMap walking network with accessibility tags and the Mapillary photos in the demo area, links each photo to its nearest path segment (within 15 m) and caches everything in `data/barrierfrei.sqlite`. |
| 2. Perceive | `python -m barrierfrei.perceive --limit 30` | For each linked photo, SAM 3.1 looks for stairs, kerbs, ramps, e-scooters and construction barriers; Muse Spark then judges the photo with those boxes drawn on it and returns schema-validated JSON (barriers, who can pass, confidence). Results are cached, so re-runs only pay for new photos. |

Settings (keys, demo bbox, model names) live in `barrierfrei/config.py` and can be overridden in `.env`,
e.g. `BBOX=[8.628,49.869,8.653,49.876]`.

## Development

```bash
ruff check . && ruff format --check .   # lint + formatting
pytest -q                               # tests run offline, no keys or credits needed
```

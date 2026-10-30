# Barrierefrei: step-free route agent

Plans walking routes without stairs, high kerbs or broken elevators by reading Mapillary street
photos with SAM 3.1 and Muse Spark, fusing them with OpenStreetMap and live Deutsche Bahn
elevator status. Built for the Meta Global AI Developer Hackathon (Oct 27 - Nov 4, 2026).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
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

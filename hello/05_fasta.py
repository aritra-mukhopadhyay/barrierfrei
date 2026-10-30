"""Live elevator/escalator status at a DB station (FaSta API).

Run: python hello/05_fasta.py [station_number]
Default 1126 is meant to be Darmstadt Hbf; if the name printed is wrong,
look the number up in the DB StaDa API or on the FaSta docs page.
"""

import sys

import requests
from common import env

station = sys.argv[1] if len(sys.argv) > 1 else "1126"
resp = requests.get(
    f"https://apis.deutschebahn.com/db-api-marketplace/apis/fasta/v2/stations/{station}",
    headers={
        "DB-Client-Id": env("DB_CLIENT_ID"),
        "DB-Api-Key": env("DB_API_KEY"),
        "accept": "application/json",
    },
    timeout=30,
)
resp.raise_for_status()
data = resp.json()
print(data.get("name"), f"(station {data.get('stationnumber')})")
for fac in data.get("facilities", []):
    print(f"  {fac.get('type'):<10} {fac.get('state'):<8} {fac.get('description') or ''}")

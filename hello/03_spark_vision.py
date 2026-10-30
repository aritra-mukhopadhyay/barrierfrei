"""Ask Muse Spark to judge accessibility of one street photo, as JSON.

Run: python hello/03_spark_vision.py "<image url>"
"""

import json
import sys

from common import image_data_uri, meta_client

image_url = image_data_uri(sys.argv[1])

PROMPT = """You check street photos for step-free accessibility.
Return only JSON with these keys:
{"barriers": [{"type": "stairs|high_curb|e_scooter|construction|narrow_sidewalk|steep_slope|other",
               "severity": "low|medium|high", "reason": "short"}],
 "passable_for": {"wheelchair": true, "stroller": true, "suitcase": true},
 "confidence": 0.0}"""

client = meta_client()
resp = client.chat.completions.create(
    model="muse-spark-1.3",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ],
    response_format={"type": "json_object"},
)
print(json.dumps(json.loads(resp.choices[0].message.content), indent=2))

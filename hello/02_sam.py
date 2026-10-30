"""Run SAM 3.1 on one street photo and print the boxes it finds.

Run: python hello/02_sam.py "<image url>" "stairs"
Try prompts like: stairs, curb, ramp, e-scooter, sidewalk.
"""
import re
import sys

from common import image_data_uri, meta_client

image_url = image_data_uri(sys.argv[1])
prompt = sys.argv[2] if len(sys.argv) > 2 else "stairs"

client = meta_client()
text = ""
with client.responses.stream(
    model="sam-3.1",
    input=[{
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_url},
        ],
    }],
) as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            text += event.delta

# Output is special-token text: <0f>id<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;...|>,...
boxes = re.findall(r"(\d+)<\|box;([^|]*)\|>", text)
print(f"'{prompt}': {len(boxes)} match(es)")
for obj_id, fields in boxes:
    box = dict(kv.split("=", 1) for kv in fields.split(";"))
    print(f"  object {obj_id}: x1={box['x1']} y1={box['y1']} x2={box['x2']} y2={box['y2']} (image {box['w']}x{box['h']})")
if not boxes:
    print("Raw output (first 300 chars):", text[:300])

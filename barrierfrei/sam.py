"""Call SAM 3.1 and parse its special-token output into boxes."""

import re
from dataclasses import dataclass

from openai import OpenAI

# Short noun phrases SAM looks for in every photo. Each phrase is one paid call.
PROMPTS = ("stairs", "curb", "ramp", "e-scooter", "construction barrier")


class SamError(RuntimeError):
    """SAM did not finish its answer, so its detections cannot be trusted."""


# One record per match: <object id><|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|>
_BOX = re.compile(r"(\d+)<\|box;([^|]*)\|>")


@dataclass(frozen=True)
class Detection:
    prompt: str
    object_id: int
    box: tuple[int, int, int, int]
    image_size: tuple[int, int]


def parse_output(prompt: str, text: str) -> list[Detection]:
    detections = []
    for object_id, fields in _BOX.findall(text):
        values = dict(kv.split("=", 1) for kv in fields.split(";"))  # parse by key, not position
        detections.append(
            Detection(
                prompt=prompt,
                object_id=int(object_id),
                box=(int(values["x1"]), int(values["y1"]), int(values["x2"]), int(values["y2"])),
                image_size=(int(values["w"]), int(values["h"])),
            )
        )
    return detections


def segment(client: OpenAI, model: str, image_uri: str, prompt: str) -> list[Detection]:
    text = ""
    status = None
    with client.responses.stream(
        model=model,
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_uri},
                ],
            }
        ],
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                text += event.delta
            elif event.type in ("response.completed", "response.failed", "response.incomplete"):
                status = event.type
            elif event.type == "error":
                raise SamError(f"SAM stream error for {prompt!r}: {event.message}")
    # A cut-off stream looks like "no detections"; raise so the photo stays unjudged and is retried.
    if status != "response.completed":
        raise SamError(f"SAM stream for {prompt!r} ended without completing ({status or 'no final event'})")
    return parse_output(prompt, text)

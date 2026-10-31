"""Ask Muse Spark to judge a photo's step-free accessibility, with schema-validated output."""

import logging
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from barrierfrei.sam import Detection

log = logging.getLogger(__name__)

BarrierType = Literal[
    "stairs",
    "high_curb",
    "e_scooter",
    "construction",
    "narrow_sidewalk",
    "steep_slope",
    "rough_surface",
    "other",
]


class Barrier(BaseModel):
    type: BarrierType
    severity: Literal["low", "medium", "high"]
    reason: str = Field(max_length=300)


class Passable(BaseModel):
    wheelchair: bool
    stroller: bool
    suitcase: bool


class Judgement(BaseModel):
    barriers: list[Barrier]
    passable_for: Passable
    confidence: float = Field(ge=0, le=1)


PROMPT = """You check street-level photos for step-free accessibility on the walking path.
Red boxes mark objects an automatic detector found: {detections}.
The detector can be wrong; trust what you see. Ignore things not on the walking path.
Answer only with JSON matching this schema:
{schema}"""


def describe(detections: list[Detection]) -> str:
    if not detections:
        return "none"
    return ", ".join(f"{d.prompt} at {d.box}" for d in detections)


def judge(
    client: OpenAI, model: str, image_uri: str, detections: list[Detection], attempts: int = 2
) -> Judgement:
    prompt = PROMPT.format(detections=describe(detections), schema=Judgement.model_json_schema())
    last_error: ValidationError | None = None
    for attempt in range(1, attempts + 1):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
        )
        try:
            # Never trust model output blindly: validate it against the schema.
            return Judgement.model_validate_json(resp.choices[0].message.content or "")
        except ValidationError as err:
            log.warning("Judgement failed validation (attempt %d/%d): %s", attempt, attempts, err)
            last_error = err
    raise RuntimeError("Model did not return a valid judgement") from last_error

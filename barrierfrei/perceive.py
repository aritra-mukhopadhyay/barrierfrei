"""Step 2 of the pipeline: SAM finds candidate barriers, Muse Spark judges each photo.

Run: python -m barrierfrei.perceive --limit 30
Already-judged photos are skipped, so re-running only pays for new photos.
"""

import argparse
import logging

from openai import OpenAI

from barrierfrei import images, judge, sam, store
from barrierfrei.config import get_settings, require
from barrierfrei.http import make_session

log = logging.getLogger("barrierfrei.perceive")


def make_client(settings) -> OpenAI:
    return OpenAI(
        base_url=settings.meta_base_url,
        api_key=require(settings.model_api_key, "MODEL_API_KEY"),
        max_retries=4,  # the SDK retries 429/5xx with backoff
        timeout=120,
    )


def perceive_photo(client: OpenAI, session, settings, conn, photo_id: str, url: str) -> judge.Judgement:
    """Run SAM and Spark on one photo and store the result."""
    raw = images.load_photo(session, photo_id, url, settings.data_dir / "photos")
    raw_uri = images.data_uri(raw)
    detections = [d for p in sam.PROMPTS for d in sam.segment(client, settings.sam_model, raw_uri, p)]
    annotated = images.draw_boxes(raw, [(d.prompt, d.box) for d in detections])
    verdict = judge.judge(client, settings.spark_model, images.data_uri(annotated), detections)
    store.save_perception(conn, photo_id, detections, verdict, settings.spark_model)
    log.info(
        "%s: %d detections, barriers=%s",
        photo_id,
        len(detections),
        [b.type for b in verdict.barriers] or "none",
    )
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30, help="max photos to process in this run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    client = make_client(settings)
    session = make_session()
    conn = store.connect(settings.db_path)
    todo = store.photos_to_judge(conn, args.limit)
    log.info("%d photos to judge (%d SAM calls)", len(todo), len(todo) * len(sam.PROMPTS))

    done = failed = 0
    for photo_id, url in todo:
        try:
            perceive_photo(client, session, settings, conn, photo_id, url)
            done += 1
        except Exception:  # one bad photo must not stop the batch
            failed += 1
            log.exception("Photo %s failed", photo_id)
    log.info("Judged %d photos, %d failed", done, failed)


if __name__ == "__main__":
    main()

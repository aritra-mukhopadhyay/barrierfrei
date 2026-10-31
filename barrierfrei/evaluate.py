"""Measure perception against OSM ground truth: does Spark flag steps and pass plain footways?

Run: python -m barrierfrei.evaluate --n 20
Photos that are already judged are reused, so re-running after a prompt change only costs
the photos whose judgements you deleted. Writes data/eval/report.csv for spot-checking.
"""

import argparse
import csv
import logging
from dataclasses import dataclass

from barrierfrei import store
from barrierfrei.config import get_settings
from barrierfrei.http import make_session
from barrierfrei.judge import Judgement
from barrierfrei.perceive import make_client, perceive_photo

log = logging.getLogger("barrierfrei.evaluate")


@dataclass(frozen=True)
class Score:
    tp: int  # steps flagged as not wheelchair-passable
    fn: int  # steps missed
    fp: int  # footway wrongly flagged
    tn: int  # footway correctly passed

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0


def flagged(verdict: Judgement) -> bool:
    """The decision routing cares about: may a wheelchair user be sent here?"""
    return not verdict.passable_for.wheelchair


def score(results: list[tuple[str, Judgement]]) -> Score:
    tp = sum(1 for label, v in results if label == "steps" and flagged(v))
    fn = sum(1 for label, v in results if label == "steps" and not flagged(v))
    fp = sum(1 for label, v in results if label == "footway" and flagged(v))
    tn = sum(1 for label, v in results if label == "footway" and not flagged(v))
    return Score(tp, fn, fp, tn)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20, help="photos per label")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    conn = store.connect(settings.db_path)
    client, session = make_client(settings), make_session()

    results: list[tuple[str, Judgement]] = []
    rows = []
    for label in ("steps", "footway"):
        sample = store.eval_sample(conn, label, args.n)
        log.info("%s: %d photos", label, len(sample))
        for photo_id, url, segment_id in sample:
            verdict = store.load_judgement(conn, photo_id)
            if verdict is None:
                try:
                    verdict = perceive_photo(client, session, settings, conn, photo_id, url)
                except Exception:
                    log.exception("Photo %s failed; left out of the score", photo_id)
                    continue
            results.append((label, verdict))
            rows.append(
                {
                    "label": label,
                    "flagged": flagged(verdict),
                    "correct": flagged(verdict) == (label == "steps"),
                    "barriers": ";".join(b.type for b in verdict.barriers),
                    "confidence": verdict.confidence,
                    "photo_id": photo_id,
                    "segment_id": segment_id,
                    "mapillary": f"https://www.mapillary.com/app/?pKey={photo_id}",
                }
            )

    s = score(results)
    report = settings.data_dir / "eval" / "report.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["label"])
        writer.writeheader()
        writer.writerows(rows)
    log.info("steps: %d/%d flagged (recall %.0f%%)", s.tp, s.tp + s.fn, 100 * s.recall)
    log.info("footway: %d/%d wrongly flagged", s.fp, s.fp + s.tn)
    log.info("precision %.0f%%; report: %s", 100 * s.precision, report)


if __name__ == "__main__":
    main()

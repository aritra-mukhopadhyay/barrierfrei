from types import SimpleNamespace

import pytest

from barrierfrei.sam import SamError, parse_output, segment

# Shape taken from Meta's "Read SAM segmentation output" docs (mask payloads shortened).
OUTPUT = (
    "<0f>0<|box;x1=715;y1=315;x2=1023;y2=370;w=1024;h=768|><|mask;x=0;y=0;data=27,60,~!!!!M|>,"
    "1<|box;x1=0;y1=442;x2=51;y2=469;w=1024;h=768;c=0.91|><|mask;x=0;y=0;c=0.91;data=3,5,~!!|>"
)


def test_parse_output_reads_every_box():
    detections = parse_output("curb", OUTPUT)
    assert [d.object_id for d in detections] == [0, 1]
    assert detections[0].box == (715, 315, 1023, 370)
    assert detections[1].image_size == (1024, 768)  # works with the optional c= field too
    assert all(d.prompt == "curb" for d in detections)


def test_parse_output_handles_no_match():
    assert parse_output("stairs", "") == []


class _FakeStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *exc):
        return False


def _client(*events):
    stream = _FakeStream([SimpleNamespace(**e) for e in events])
    return SimpleNamespace(responses=SimpleNamespace(stream=lambda **kw: stream))


def test_segment_returns_detections_when_stream_completes():
    client = _client(
        {"type": "response.output_text.delta", "delta": OUTPUT},
        {"type": "response.completed"},
    )
    assert len(segment(client, "sam", "data:", "curb")) == 2


@pytest.mark.parametrize("last", [[{"type": "response.incomplete"}], [{"type": "response.failed"}], []])
def test_segment_raises_when_stream_does_not_complete(last):
    client = _client({"type": "response.output_text.delta", "delta": OUTPUT[:40]}, *last)
    with pytest.raises(SamError):
        segment(client, "sam", "data:", "curb")

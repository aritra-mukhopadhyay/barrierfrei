import json
from types import SimpleNamespace

import pytest

from barrierfrei import judge

VALID = {
    "barriers": [{"type": "high_curb", "severity": "medium", "reason": "kerb without lowering"}],
    "passable_for": {"wheelchair": False, "stroller": True, "suitcase": True},
    "confidence": 0.8,
}


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_valid_judgement_is_parsed():
    result = judge.judge(FakeClient([json.dumps(VALID)]), "m", "data:", [])
    assert result.passable_for.wheelchair is False
    assert result.barriers[0].type == "high_curb"


def test_invalid_reply_is_retried_then_accepted():
    bad = json.dumps({**VALID, "confidence": 3})  # out of range
    result = judge.judge(FakeClient([bad, json.dumps(VALID)]), "m", "data:", [])
    assert result.confidence == 0.8


def test_gives_up_after_repeated_invalid_replies():
    with pytest.raises(RuntimeError):
        judge.judge(FakeClient(["not json", "{}"]), "m", "data:", [])

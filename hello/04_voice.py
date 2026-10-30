"""Transcribe a short voice request with Muse Voice Transcribe.

Record yourself saying e.g. "I have a stroller, take me from Luisenplatz to the main station",
then convert:  ffmpeg -i request.m4a -ac 1 -ar 24000 -c:a pcm_s16le -map_metadata -1 request.wav
Run: python hello/04_voice.py request.wav
"""
import json
import sys

import requests

from common import META_BASE_URL, env

wav_path = sys.argv[1]
settings = {"mode": "PUSH_TO_TALK", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
            "languageBias": ["English", "German"]}

with open(wav_path, "rb") as f:
    resp = requests.post(
        f"{META_BASE_URL}/asr/transcribe",
        headers={"Authorization": f"Bearer {env('MODEL_API_KEY')}"},
        files={
            "request": (None, json.dumps(settings), "application/json"),
            "audio": (wav_path, f, "audio/wav"),
        },
        timeout=60,
    )
resp.raise_for_status()
print(resp.json()["transcript"])

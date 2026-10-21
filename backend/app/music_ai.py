"""Selected real PCM audio -> bounded WAV requests -> Music Flamingo observations."""
import base64
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .ai_provider import AIError, Provider

PIPELINE_VERSION = "music-listening-v1"


def audio_path(settings, relative):
    root = (settings.storage_dir / "audio").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AIError("Selected audio file is unavailable. Complete separation/analysis and retry.")
    return path


def audio_inputs(settings, project, items):
    """Snapshot server-owned file references and exact offset-adjusted ranges."""
    result = []
    for item in items:
        source = next((s for s in project["tracks"] if s["id"] == item["source_id"]), None)
        vocal = next((v for v in source.get("stems", []) if v["id"] == item["track_id"]), None) if source else None
        if not vocal or not vocal.get("file"):
            raise AIError("Selected vocal file is unavailable. Complete separation and retry.")
        start, end = item["source_range_seconds"]
        common = {"source_id": item["source_id"], "track_id": item["track_id"], "name": item["name"],
                  "range_seconds": item["range_seconds"], "source_range_seconds": [start, end],
                  "offset_seconds": item["offset_seconds"], "derived": bool(vocal.get("derived")),
                  "transform": vocal.get("transform")}
        # A transformed vocal can have a different timeline. Never claim its original mix is aligned.
        if not vocal.get("derived") and source.get("original_file"):
            result.append({**common, "kind": "original", "file": source["original_file"]})
        result.append({**common, "kind": "vocals", "file": vocal["file"]})
    for entry in result:
        audio_path(settings, entry["file"])
        if entry["source_range_seconds"][1] - entry["source_range_seconds"][0] < .2:
            raise AIError("Select at least 0.2 seconds of audio on every compared track.")
    return result


def wav_chunks(path: Path, start: float, end: float, max_seconds=30):
    """Read only the chosen frames; normalize format, never normalize listening volume."""
    with sf.SoundFile(path) as source:
        sr = source.samplerate
        if sr > 192000 or source.channels > 8 or sr < 1000:
            raise AIError("Unsupported audio sample rate/channel count.")
        first, last = round(start * sr), round(end * sr)
        if first < 0 or last > source.frames + 1 or last - first < math.ceil(.2 * sr):
            raise AIError("Selected audio range is outside the file or shorter than 0.2 seconds.")
        last = min(last, source.frames)
        count = math.ceil((last - first) / (max_seconds * sr))
        # Even partitions avoid a tiny final chunk. Every selected frame is included once.
        for i in range(count):
            a, b = first + (last - first) * i // count, first + (last - first) * (i + 1) // count
            source.seek(a)
            samples = source.read(b - a, dtype="float32", always_2d=True).mean(axis=1)
            if len(samples) != b - a or not np.isfinite(samples).all():
                raise AIError("Invalid PCM audio in the selected range.")
            if sr != 24000:
                factor = math.gcd(sr, 24000)
                samples = resample_poly(samples, 24000 // factor, sr // factor)
            clipped = int(np.count_nonzero(np.abs(samples) > 1))
            out = BytesIO()
            sf.write(out, samples, 24000, format="WAV", subtype="PCM_16")
            data = out.getvalue()
            yield data, {"source_range_seconds": [a / sr, b / sr], "source_frames": [a, b],
                         "source_sample_rate": sr, "sample_rate": 24000, "channels": 1,
                         "format": "wav", "subtype": "PCM_16", "bytes": len(data),
                         "sha256": sha256(data).hexdigest(), "clipped_samples": clipped}


def listen(settings, provider: Provider, inputs, language="ko", on_result=lambda *_: None):
    observations = []
    for entry in inputs:
        path = audio_path(settings, entry["file"])
        start, end = entry["source_range_seconds"]
        for data, metadata in wav_chunks(path, start, end, settings.audio_clip_seconds):
            prompt = (
                "Listen to this music excerpt. Describe audible genre, instruments, tempo/rhythm, timbre, "
                "vocal characteristics, mood and changes within this excerpt. Distinguish observations from "
                "uncertain interpretations. Do not invent lyrics or exact BPM/pitch measurements, score/rank "
                "singers, or assert vocal register/technique without evidence. "
                "This is " + ("an original music mix. " if entry["kind"] == "original" else
                               "an isolated vocal stem; separation artifacts/leakage may be present. ") +
                ("Respond in English." if language == "en" else "한국어로 응답하세요.")
            )
            completion = provider.complete(settings, [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(data).decode("ascii"), "format": "wav"}},
            ]}], settings.audio_max_tokens)
            observation = {k: v for k, v in entry.items() if k != "file"}
            observation.update(audio=metadata, **completion)
            observations.append(observation)
            on_result(observations)
    if not observations:
        raise AIError("No audio excerpts were supplied to Music Flamingo.")
    return observations

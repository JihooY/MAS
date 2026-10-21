"""Aligned full mix/vocal/instrumental listening and whole-song context."""
import base64
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, find_peaks

from .ai_provider import AIError, Provider

PIPELINE_VERSION = "music-context-v2"
KINDS = ("full_mix", "vocals", "instrumental")


def audio_path(settings, relative):
    root = (settings.storage_dir / "audio").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AIError("Selected audio file is unavailable. Complete separation/analysis and retry.")
    return path


def audio_inputs(settings, project, items):
    """Snapshot aligned references; never substitute a vocal for a missing original."""
    result = []
    for item in items:
        source = next((s for s in project["tracks"] if s["id"] == item["source_id"]), None)
        vocal = next((v for v in source.get("stems", []) if v["id"] == item["track_id"]), None) if source else None
        if not vocal or not vocal.get("file"):
            raise AIError("Selected vocal file is unavailable. Complete separation and retry.")
        if vocal.get("derived"):
            raise AIError("Three-way context analysis requires the original vocal timeline. Select the base vocal; transformed playback and DSP remain available.")
        if source.get("legacy_vocal") or not source.get("original_file"):
            raise AIError("Full Mix is missing. Upload and separate the original music for three-way analysis.")
        stems = [v for v in source.get("stems", []) if v.get("kind") not in {"vocals", "original"}]
        if not stems or any(v.get("status") != "ready" or not v.get("file") for v in stems):
            raise AIError("Instrumental stems are missing. Separate the original music again.")
        accompaniment = [v["file"] for v in stems]
        start, end = item["source_range_seconds"]
        if end - start < .2 - 1e-6:
            raise AIError("Select at least 0.2 seconds of audio on every compared track.")
        common = {"source_id": source["id"], "track_id": vocal["id"], "name": source["name"],
                  "range_seconds": item["range_seconds"], "source_range_seconds": [start, end],
                  "offset_seconds": item["offset_seconds"], "scope": "section",
                  "section_label": item.get("section_label", ""), "sections": source.get("sections", []),
                  "reference_file": source["original_file"]}
        for kind, files in (("full_mix", [source["original_file"]]), ("vocals", [vocal["file"]]),
                            ("instrumental", accompaniment)):
            result.append({**common, "kind": kind, "files": files,
                           "provides_song_overview": kind == "full_mix" and start == 0 and abs(end-source["duration"]) < .002})
        if not result[-3]["provides_song_overview"]:
            result.append({**common, "kind": "full_mix", "scope": "song_overview", "files": [source["original_file"]],
                           "source_range_seconds": [0, source["duration"]], "range_seconds": [0, source["duration"]],
                           "offset_seconds": 0, "provides_song_overview": True, "section_label": ""})
    for entry in result:
        for file in entry["files"] + [entry["reference_file"]]:
            audio_path(settings, file)
    return result


def chunk_ranges(path, start, end, max_seconds=30, sections=()):
    """User boundaries first; otherwise quiet RMS boundaries within the API limit.

    This is request packing, not an automatic verse/chorus detector.
    """
    with sf.SoundFile(path) as source:
        sr = source.samplerate
        first, last = round(start*sr), round(end*sr)
        if abs(last-source.frames) <= max(1, round(.001*sr)):
            # UI durations are rounded to milliseconds; cover the actual final frame.
            last = source.frames
        if first < 0 or last > source.frames+1 or last-first < math.ceil(.2*sr):
            raise AIError("Selected audio range is outside the file or shorter than 0.2 seconds.")
        if sr > 192000 or sr < 1000 or source.channels > 8:
            raise AIError("Unsupported audio sample rate/channel count.")
        last = min(last, source.frames)
        manual = sorted({round(s[k]*sr) for s in sections for k in ("start", "end")
                         if first+.2*sr <= s[k]*sr <= last-.2*sr})
        cursor = first
        while cursor < last:
            limit = min(last, cursor+round(max_seconds*sr))
            candidates = [v for v in manual if cursor+.2*sr <= v <= limit]
            reason = "end"
            if candidates:
                stop, reason = candidates[0], "manual_section"
            elif limit < last:
                # Search near the request limit, not equal-length partitions.
                a = cursor + round(.75*max_seconds*sr)
                window = max(1, round(.05*sr))
                source.seek(a)
                y = source.read(limit-a, dtype="float32", always_2d=True).mean(axis=1)
                blocks = len(y)//window
                energy = np.mean(y[:blocks*window].reshape(blocks, window)**2, axis=1) if blocks else np.array([])
                stop = a+(int(np.argmin(energy))+1)*window if len(energy) else limit
                stop, reason = min(stop, limit), "quiet_boundary_candidate"
            else:
                stop = last
            if 0 < last-stop < .2*sr:
                stop = last-round(.2*sr)
            if stop <= cursor:
                raise AIError("Could not construct a valid audio request interval.")
            yield cursor/sr, stop/sr, reason
            cursor = stop


def pcm_segment(paths, start, end):
    """Sum non-vocal stems on their existing sample-accurate timeline."""
    samples, native_sr, channels = None, None, None
    first = last = 0
    for path in paths:
        with sf.SoundFile(path) as source:
            sr = source.samplerate
            a, b = round(start*sr), round(end*sr)
            if abs(b-source.frames) <= max(1, round(.001*sr)):
                b = source.frames
            if sr > 192000 or sr < 1000 or source.channels > 8 or a < 0 or b > source.frames+1:
                raise AIError("Stem duration/sample rate is incompatible with the reference mix.")
            source.seek(a)
            y = source.read(min(b, source.frames)-a, dtype="float32", always_2d=True)
            if not len(y) or not np.isfinite(y).all():
                raise AIError("Invalid PCM audio in the selected range.")
            if samples is not None and (sr != native_sr or y.shape != samples.shape):
                raise AIError("Instrumental stems have mismatched sample rates, channels or lengths.")
            samples = y if samples is None else samples+y
            native_sr, channels, first, last = sr, source.channels, a, a+len(y)
    if samples is None:
        raise AIError("No audio files supplied.")
    mono = samples.mean(axis=1)
    if native_sr != 24000:
        factor = math.gcd(native_sr, 24000)
        mono = resample_poly(mono, 24000//factor, native_sr//factor)
    return mono, {"source_range_seconds": [first/native_sr, last/native_sr], "source_frames": [first, last],
                  "source_sample_rate": native_sr, "source_channels": channels, "stem_count": len(paths)}


def acoustic_features(samples, sr=24000):
    """Waveform measurements only; no pitch-accuracy/technique score from DSP."""
    size = max(1, round(.05*sr))
    env = np.array([np.sqrt(np.mean(v*v)) for v in np.array_split(samples, max(1, math.ceil(len(samples)/size)))])
    db = 20*np.log10(np.maximum(env, 1e-8))
    onset = np.maximum(np.diff(env, prepend=env[0]), 0)
    peaks, _ = find_peaks(onset, height=max(float(onset.max())*.2, .001), distance=3)
    tempo = None
    if len(samples)/sr >= 3 and len(peaks) >= 3:
        # An envelope periodicity candidate, never certified beat tracking.
        autocorr = np.correlate(onset-onset.mean(), onset-onset.mean(), mode="full")[len(onset)-1:]
        lo, hi = 5, min(40, len(autocorr))
        if hi > lo and np.max(autocorr[lo:hi]) > 0:
            tempo = round(60/(.05*(lo+int(np.argmax(autocorr[lo:hi])))), 1)
    spectrum = np.abs(np.fft.rfft(samples))
    centroid = float(np.dot(np.fft.rfftfreq(len(samples), 1/sr), spectrum)/max(float(spectrum.sum()), 1e-12))
    return {"rms_dbfs": round(float(20*np.log10(max(float(np.sqrt(np.mean(samples*samples))), 1e-8))), 2),
            "peak_dbfs": round(float(20*np.log10(max(float(np.abs(samples).max()), 1e-8))), 2),
            "dynamic_range_db": round(float(np.percentile(db, 95)-np.percentile(db, 10)), 2),
            "spectral_centroid_hz": round(centroid, 1), "onset_candidates": len(peaks),
            "tempo_candidate_bpm": tempo, "method": "waveform-rms-spectrum-onset-v1"}


def encode_wav(samples, metadata):
    features = acoustic_features(samples)
    peak = float(np.abs(samples).max())
    gain = min(1., .999/peak) if peak else 1.
    out = BytesIO()
    sf.write(out, samples*gain, 24000, format="WAV", subtype="PCM_16")
    data = out.getvalue()
    return data, {**metadata, "sample_rate": 24000, "channels": 1, "format": "wav", "subtype": "PCM_16",
                  "bytes": len(data), "sha256": sha256(data).hexdigest(), "pcm_gain": gain,
                  "over_range_samples": int(np.count_nonzero(np.abs(samples)>1)), "dsp_features": features}


def wav_chunks(path: Path, start: float, end: float, max_seconds=30):
    for a, b, reason in chunk_ranges(path, start, end, max_seconds):
        samples, meta = pcm_segment([path], a, b)
        yield encode_wav(samples, {**meta, "boundary_reason": reason})


def structured_observations(content):
    try:
        value = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
        fields = ("musical_features", "related_evidence", "uncertainties")
        if isinstance(value, dict) and all(isinstance(value.get(k), list) and all(isinstance(v, str) for v in value[k]) for k in fields):
            return {**{k: value[k] for k in fields}, "structured": True}
    except ValueError:
        pass
    return {"musical_features": [], "related_evidence": [], "structured": False,
            "uncertainties": ["Unstructured listening response; consult raw_response before interpreting details."]}


def listen(settings, provider: Provider, inputs, language="ko", on_result=lambda *_: None):
    observations, plans = [], {}
    for entry in inputs:
        # The standalone connection probe is deliberately full-mix only.
        kind = "full_mix" if entry["kind"] == "original" else entry["kind"]
        files = entry.get("files") or [entry["file"]]
        paths = [audio_path(settings, f) for f in files]
        reference = audio_path(settings, entry.get("reference_file", files[0]))
        start, end = entry["source_range_seconds"]
        key = (entry["source_id"], entry.get("scope", "section"), start, end)
        if key not in plans:
            plans[key] = list(chunk_ranges(reference, start, end, settings.audio_clip_seconds, entry.get("sections", [])))
        for a, b, reason in plans[key]:
            samples, meta = pcm_segment(paths, a, b)
            data, metadata = encode_wav(samples, {**meta, "boundary_reason": reason})
            focus = {
                "full_mix": "Analyze the full musical context: genre/style, structure/development, arrangement, emotional contour as a listening interpretation, tension/release, dynamics, and audible vocal/accompaniment interaction.",
                "vocals": "Analyze this isolated vocal: timbre, phrasing, articulation, vibrato, emotional expression and possible register/technique changes as uncertain auditory interpretations. Do not infer how it fits the unheard accompaniment.",
                "instrumental": "Analyze this vocal-excluded accompaniment: instrument roles, rhythm/groove, harmonic texture, dynamics, arrangement changes and tension/release. Do not infer vocal quality from this input.",
            }.get(kind, "Analyze the audible musical elements.")
            prompt = (focus+" Only describe the supplied excerpt; do not invent song-wide structure, lyrics or artist intent. "
                      "Separation leakage may affect stems. Numeric BPM/pitch and vocal technique are uncertain interpretations, not objective measurements. "
                      "Return a JSON object with musical_features, related_evidence and uncertainties, each an array of strings. "
                      "Describe evidence with times relative to this excerpt. " +
                      ("Use English." if language == "en" else "설명은 한국어로 작성하세요."))
            completion = provider.complete(settings, [{"role": "user", "content": [
                {"type": "text", "text": prompt}, {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(data).decode("ascii"), "format": "wav"}},
            ]}], settings.audio_max_tokens)
            group = sha256(json.dumps([entry["source_id"], entry.get("scope", "section"), a, b]).encode()).hexdigest()[:16]
            observation = {k: v for k, v in entry.items() if k not in {"file", "files", "reference_file", "sections"}}
            observation.update(kind=kind, analysis_type=kind, audio=metadata, group_id=group,
                               evidence_id=f"audio:{group}:{kind}", raw_response=completion["content"], **completion,
                               **structured_observations(completion["content"]))
            observations.append(observation)
            on_result(observations)
    if not observations:
        raise AIError("No audio excerpts were supplied to Music Flamingo.")
    return observations

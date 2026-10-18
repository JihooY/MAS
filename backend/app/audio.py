"""Real decoded PCM -> NumPy/librosa measurements. No synthetic analysis values."""

import hashlib
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import librosa
import numpy as np
import soundfile as sf
from scipy import signal

ENGINE = "vocal-pyin-v1"
SR = 22050
HOP = 512


def read_audio(path: Path, max_duration: int):
    # Check duration before allocating the full decoded stream.
    with sf.SoundFile(path) as f:
        if (
            f.channels > 8
            or f.samplerate > 192000
            or f.frames / f.samplerate > max_duration
        ):
            raise ValueError(f"최대 {max_duration}초, 8채널, 192kHz까지 지원합니다.")
        # Downmix in blocks to avoid allocating all native multichannel PCM.
        pieces = [
            block.mean(axis=1)
            for block in f.blocks(blocksize=65536, dtype="float32", always_2d=True)
        ]
        y = np.concatenate(pieces) if pieces else np.empty(0, dtype="float32")
        sr = f.samplerate
    if len(y) < sr * 0.2 or not np.all(np.isfinite(y)):
        raise ValueError("0.2초 이상의 유효한 오디오가 필요합니다.")
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return y, SR


def decode(path: Path, output: Path, max_duration: int):
    try:
        y, sr = read_audio(path, max_duration)
    except (sf.LibsndfileError, RuntimeError):
        # Bundled FFmpeg fallback; stop after max+1 seconds to bound decode memory.
        proc = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-v",
                "error",
                "-i",
                str(path),
                "-t",
                str(max_duration + 1),
                "-ac",
                "1",
                "-ar",
                str(SR),
                "-f",
                "f32le",
                "pipe:1",
            ],
            capture_output=True,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode:
            raise ValueError(
                "오디오를 읽을 수 없습니다. 올바른 WAV, MP3, FLAC 파일인지 확인하세요."
            )
        y = np.frombuffer(proc.stdout, dtype="<f4").copy()
        sr = SR
        if not sr * 0.2 <= len(y) <= sr * max_duration or not np.all(np.isfinite(y)):
            raise ValueError(f"오디오 길이는 0.2~{max_duration}초여야 합니다.")
    sf.write(output, y, sr, subtype="FLOAT")
    return {
        "duration": round(len(y) / sr, 3),
        "sample_rate": sr,
        "channels": 1,
        "peak": round(float(np.max(np.abs(y))), 5),
    }


def transform(
    source: Path,
    output: Path,
    semitones: float,
    speed: float,
    normalize: bool,
    max_duration: int,
):
    y, sr = read_audio(source, max_duration)
    if len(y) / sr / speed > max_duration:
        raise ValueError(f"속도 변경 후 길이가 제한({max_duration}초)을 초과합니다.")
    if semitones:
        y = librosa.effects.pitch_shift(y, sr=sr, n_steps=semitones)
    if speed != 1:
        y = librosa.effects.time_stretch(y, rate=speed)
    if normalize and np.max(np.abs(y)) > 0:
        y = y * (0.95 / np.max(np.abs(y)))
    sf.write(output, y, sr, subtype="FLOAT")
    return {
        "duration": round(len(y) / sr, 3),
        "sample_rate": sr,
        "channels": 1,
        "peak": round(float(np.max(np.abs(y))), 5),
    }


def rounded(values, digits=3):
    return [round(float(x), digits) if np.isfinite(x) else None for x in values]


def analyze(path: Path, max_duration: int, progress=lambda *_: None):
    y, sr = read_audio(path, max_duration)
    progress(25, "파형 · 다이내믹 분석")
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    progress(38, "확률 기반 pYIN 음정 추적 · CPU")
    # Chunking bounds pYIN's dynamic-programming matrix for long recordings.
    f0s, voices, probs = [], [], []
    size = (sr * 20 // HOP) * HOP
    for start in range(0, len(y), size):
        segment = y[start : start + size]
        if len(segment) < 2048:
            segment = np.pad(segment, (0, 2048 - len(segment)))
        f0, voiced, prob = librosa.pyin(
            segment,
            sr=sr,
            fmin=65.4,
            fmax=1568,
            frame_length=2048,
            hop_length=HOP,
            fill_na=np.nan,
        )
        count = min(len(f0), int(np.ceil((len(y) - start) / HOP)), size // HOP)
        f0s.extend(f0[:count])
        voices.extend(voiced[:count])
        probs.extend(prob[:count])
        progress(
            38 + int(35 * min(start + size, len(y)) / len(y)),
            "확률 기반 pYIN 음정 추적 · CPU",
        )
    f0 = np.asarray(f0s)
    prob = np.asarray(probs)
    # Centered STFT/RMS has one extra terminal frame on exact hop boundaries.
    count = len(rms)
    f0 = np.pad(f0, (0, max(0, count - len(f0))), constant_values=np.nan)[:count]
    prob = np.pad(prob, (0, max(0, count - len(prob))), constant_values=0)[:count]
    voiced = np.pad(
        np.asarray(voices, dtype=bool),
        (0, max(0, count - len(voices))),
        constant_values=False,
    )[:count]
    frame_rms = rms
    active = frame_rms > max(0.002, float(np.max(rms)) * 0.025)
    reliable = voiced & (prob >= 0.15) & active
    f0[~reliable] = np.nan
    midi = librosa.hz_to_midi(f0)
    valid = midi[np.isfinite(midi)]
    residual = (valid - np.round(valid)) * 100
    progress(80, "스펙트럼 · 온셋 · 비브라토 후보 분석")
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    centroid = librosa.feature.spectral_centroid(S=spec, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(S=spec)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, units="time"
    )
    tempo = None
    if len(y) > sr * 3 and np.max(onset_env) > 0:
        raw_tempo = librosa.feature.tempo(
            onset_envelope=onset_env, sr=sr, hop_length=HOP
        )
        tempo = round(float(raw_tempo[0]), 1)
    # Only sustained voiced runs >=1.5s; candidates, never a technique classification.
    vibrato = []
    runs = np.split(
        np.arange(len(f0)), np.where(np.diff(reliable.astype(int)) != 0)[0] + 1
    )
    frame_rate = sr / HOP
    for run in runs:
        if len(run) < frame_rate * 1.5 or not reliable[run[0]]:
            continue
        cents = midi[run] * 100
        if np.ptp(cents) > 400:
            continue
        detrended = signal.detrend(cents)
        freq, power = signal.periodogram(detrended, fs=frame_rate)
        band = (freq >= 4) & (freq <= 9)
        ratio = float(np.sum(power[band]) / max(np.sum(power), 1e-12))
        if ratio > 0.4 and np.any(band):
            rate = float(freq[band][np.argmax(power[band])])
            vibrato.append(
                {
                    "start": round(run[0] / frame_rate, 2),
                    "end": round(run[-1] / frame_rate, 2),
                    "rate_hz": round(rate, 2),
                    "width_cents": round(
                        float(
                            np.percentile(detrended, 95) - np.percentile(detrended, 5)
                        ),
                        1,
                    ),
                    "periodicity": round(ratio, 2),
                }
            )
    stride = max(1, int(np.ceil(len(times) / 1400)))
    wave = np.array_split(y, min(1000, len(y)))
    spec_times = np.linspace(0, spec.shape[1] - 1, min(280, spec.shape[1])).astype(int)
    # Log-frequency bands, 65Hz through Nyquist; all cells computed from the STFT.
    edges = np.geomspace(65, sr / 2, 81)
    fft_freq = librosa.fft_frequencies(sr=sr, n_fft=2048)
    bands = np.stack(
        [
            np.mean(spec[(fft_freq >= lo) & (fft_freq < hi)][:, spec_times], axis=0)
            if np.any((fft_freq >= lo) & (fft_freq < hi))
            else np.zeros(len(spec_times))
            for lo, hi in zip(edges[:-1], edges[1:])
        ]
    )
    db = librosa.amplitude_to_db(bands, ref=np.max)
    summary = {
        "duration": round(len(y) / sr, 3),
        "sample_rate": sr,
        "rms_db": round(float(20 * np.log10(max(np.sqrt(np.mean(y**2)), 1e-8))), 2),
        "dynamic_range_db": round(
            float(
                np.percentile(rms_db[active], 95) - np.percentile(rms_db[active], 10)
            ),
            2,
        )
        if np.any(active)
        else 0,
        "peak_db": round(float(20 * np.log10(max(float(np.max(np.abs(y))), 1e-8))), 2),
        "clipping_percent": round(float(np.mean(np.abs(y) >= 0.999) * 100), 3),
        "voiced_percent": round(float(np.mean(reliable) * 100), 1),
        "pitch_confidence": round(float(np.mean(prob[reliable])), 3)
        if np.any(reliable)
        else 0,
        "pitch_low_note": librosa.midi_to_note(float(np.percentile(valid, 5)))
        if len(valid)
        else None,
        "pitch_high_note": librosa.midi_to_note(float(np.percentile(valid, 95)))
        if len(valid)
        else None,
        "pitch_median_hz": round(float(np.nanmedian(f0)), 2) if len(valid) else None,
        "note_center_deviation_cents": round(float(np.median(np.abs(residual))), 2)
        if len(valid)
        else None,
        "spectral_centroid_hz": round(float(np.mean(centroid[active])), 1)
        if np.any(active)
        else 0,
        "spectral_flatness": round(float(np.mean(flatness[active])), 5)
        if np.any(active)
        else 0,
        "onset_count": len(onsets),
        "tempo_candidate_bpm": tempo,
    }
    warnings = [
        "단일 보컬 기준 분석입니다. 반주·리버브·잡음은 음정 추정에 영향을 줍니다.",
        "음 중심 편차는 가장 가까운 평균율 음과의 거리이며 악보 대비 음정 정확도나 가창 점수가 아닙니다.",
        "보컬만으로 추정한 BPM은 구절 강세의 주기 후보이며 곡의 확정 템포가 아닙니다.",
        "20초 분석 블록 경계의 음정은 추정 오차가 있을 수 있습니다.",
    ]
    if summary["voiced_percent"] < 20:
        warnings.append("유효한 유성음이 적어 음역·음정 해석의 신뢰도가 낮습니다.")
    return {
        "engine": ENGINE,
        "summary": summary,
        "warnings": warnings,
        "series": {
            "time": rounded(times[::stride]),
            "rms_db": rounded(rms_db[::stride]),
            "pitch_time": rounded(np.arange(len(f0))[::stride] * HOP / sr),
            "pitch_hz": rounded(f0[::stride]),
            "pitch_midi": rounded(midi[::stride]),
            "confidence": rounded(prob[::stride]),
            "centroid_hz": rounded(centroid[::stride]),
        },
        "waveform": {
            "min": rounded([np.min(w) for w in wave], 5),
            "max": rounded([np.max(w) for w in wave], 5),
        },
        "spectrogram": {
            "db": np.round(db, 1).tolist(),
            "min_db": -80,
            "duration": len(y) / sr,
            "low_hz": 65,
            "high_hz": sr / 2,
        },
        "onsets": rounded(onsets),
        "vibrato_candidates": vibrato[:30],
    }


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_analysis(path, cache_dir, max_duration, progress):
    key = f"{ENGINE}-{pcm_digest(path)}"
    target = cache_dir / f"{key}.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8")), True
    data = analyze(path, max_duration, progress)
    target.write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    return data, False


def pcm_digest(path):
    # FLOAT WAV may contain a PEAK timestamp. Header bytes are not audio identity.
    h = hashlib.sha256()
    with sf.SoundFile(path) as stream:
        h.update(f"{stream.samplerate}:{stream.channels}".encode())
        for block in stream.blocks(blocksize=65536, dtype="float32", always_2d=True):
            h.update(block.astype("<f4", copy=False).tobytes())
    return h.hexdigest()

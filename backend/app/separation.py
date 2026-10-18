"""Demucs adapter: real source separation, never a frequency-filter imitation."""
from functools import lru_cache
import importlib.util
import os
from pathlib import Path
import subprocess

import imageio_ffmpeg
import numpy as np
import soundfile as sf

MODELS = {"htdemucs": ["drums", "bass", "other", "vocals"],
          "htdemucs_6s": ["drums", "bass", "other", "vocals", "guitar", "piano"]}
LABELS = {"vocals": "보컬", "drums": "드럼", "bass": "베이스", "other": "기타 반주", "guitar": "기타", "piano": "피아노", "original": "원본 음악"}


def available():
    return all(importlib.util.find_spec(name) is not None for name in ("torch", "demucs", "torchaudio"))


def waveform(path):
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = y.mean(axis=1)
    bins = np.array_split(mono, min(1000, len(mono)))
    return {"min": [round(float(np.min(b)), 5) for b in bins], "max": [round(float(np.max(b)), 5) for b in bins]}


def prepare_original(source, output, max_duration):
    # Preserve stereo for the trained separator; vocal analysis downmixes separately.
    proc = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(source),
                           "-t", str(max_duration + 1), "-ac", "2", "-ar", "44100", "-f", "f32le", "pipe:1"],
                          capture_output=True, timeout=90,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode:
        raise ValueError("음악을 읽을 수 없습니다. 올바른 WAV, MP3, FLAC인지 확인하세요.")
    y = np.frombuffer(proc.stdout, dtype="<f4")
    if len(y) % 2 or not 44100 * 2 * .2 <= len(y) <= 44100 * 2 * max_duration or not np.isfinite(y).all():
        raise ValueError(f"음원 길이는 0.2~{max_duration}초여야 합니다.")
    y = y.reshape(-1, 2)
    sf.write(output, y, 44100, subtype="FLOAT")
    return {"duration": round(len(y) / 44100, 3), "sample_rate": 44100, "channels": 2,
            "peak": round(float(np.abs(y).max()), 5), "waveform": waveform(output)}


@lru_cache(maxsize=2)
def _load_model(name, model_dir):
    import torch
    from demucs.pretrained import get_model
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    torch.hub.set_dir(str(model_dir))
    # Demucs 4 checkpoints contain its model architecture. Only fixed, official
    # pretrained names are accepted; no user-supplied checkpoints are loaded.
    previous = os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD")
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
    try:
        model = get_model(name=name)
    finally:
        if previous is None:
            os.environ.pop("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", None)
        else:
            os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = previous
    model.eval()
    return model


def separate(source: Path, output_dir: Path, model_name: str, device: str, model_dir: Path, progress):
    if model_name not in MODELS:
        raise ValueError("지원하지 않는 분리 모델입니다.")
    if not available():
        raise ValueError("분리 엔진 미설치: uv sync 명령을 실행한 뒤 서버를 다시 시작하세요.")
    import torch
    from demucs.apply import apply_model
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device not in ("cpu", "cuda") or device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA GPU를 사용할 수 없습니다. MAS_SEPARATION_DEVICE=cpu 또는 auto로 설정하세요.")
    progress(20, "Demucs 모델 준비 · 첫 실행 시 공식 가중치 다운로드")
    model = _load_model(model_name, str(model_dir))
    y, sr = sf.read(source, dtype="float32", always_2d=True)
    if sr != model.samplerate:
        raise ValueError("분리 입력 샘플레이트가 모델과 맞지 않습니다.")
    mix = torch.from_numpy(y.T.copy())
    reference = mix.mean(0)
    mean, std = reference.mean(), reference.std().clamp_min(1e-8)
    progress(35, f"보컬 · 악기 분리 중 · {device.upper()} (음원 길이에 따라 수분 소요)")
    with torch.inference_mode():
        sources = apply_model(model, ((mix - mean) / std)[None], device=device,
                              shifts=0, split=True, segment=7, overlap=.25, progress=False, num_workers=0)[0]
        sources = (sources * std + mean).cpu().numpy()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (name, samples) in enumerate(zip(model.sources, sources)):
        if name not in LABELS:
            raise ValueError("모델의 분리 트랙 구성을 확인할 수 없습니다.")
        path = output_dir / f"{name}.wav"
        sf.write(path, samples.T, sr, subtype="FLOAT")
        results.append({"kind": name, "name": LABELS[name], "duration": len(samples[0]) / sr,
                        "sample_rate": sr, "channels": samples.shape[0], "peak": float(np.abs(samples).max()),
                        "waveform": waveform(path)})
        progress(80 + int((i+1) / len(sources) * 18), f"{LABELS[name]} 트랙 저장")
    return results

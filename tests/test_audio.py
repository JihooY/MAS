import json
import subprocess

import imageio_ffmpeg

import numpy as np
import pytest
import soundfile as sf

from backend.app import audio


def tone(path, frequency=220, duration=2, sr=22050):
    t = np.arange(int(duration * sr)) / sr
    y = 0.3 * np.sin(2 * np.pi * frequency * t)
    sf.write(path, y, sr, subtype="FLOAT")
    return path


def test_pitch_measurements_and_cache(tmp_path):
    path = tone(tmp_path / "a.wav")
    data, hit = audio.cached_analysis(path, tmp_path, 10, lambda *_: None)
    assert not hit
    assert data["summary"]["pitch_median_hz"] == pytest.approx(220, abs=3)
    assert data["summary"]["rms_db"] == pytest.approx(-13.47, abs=0.1)
    assert data["summary"]["pitch_low_note"] == "A3"
    assert len(data["series"]["time"]) == len(data["series"]["pitch_hz"])
    assert len(data["spectrogram"]["db"]) == 80
    json.dumps(data, allow_nan=False)
    assert audio.cached_analysis(path, tmp_path, 10, lambda *_: None)[1]


def test_silence_has_no_invented_pitch_and_exact_hop(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(512 * 50), 22050)
    result = audio.analyze(path, 10)
    assert result["summary"]["pitch_median_hz"] is None
    assert result["summary"]["voiced_percent"] == 0
    assert result["vibrato_candidates"] == []
    json.dumps(result, allow_nan=False)


def test_independent_pitch_and_speed_transform(tmp_path):
    source = tone(tmp_path / "source.wav")
    output = tmp_path / "changed.wav"
    meta = audio.transform(source, output, semitones=12, speed=0.8, normalize=True, max_duration=10)
    assert meta["duration"] == pytest.approx(2.5, abs=.03)
    result = audio.analyze(output, 10)
    assert result["summary"]["pitch_median_hz"] == pytest.approx(440, abs=5)
    assert meta["peak"] == pytest.approx(.95, abs=.001)


def test_length_rejected_before_decode(tmp_path):
    path = tone(tmp_path / "long.wav", duration=2)
    with pytest.raises(ValueError, match="최대"):
        audio.decode(path, tmp_path / "no.wav", 1)


def test_flac_and_stereo_resampling(tmp_path):
    path = tmp_path / "stereo.flac"
    sr = 44100
    t = np.arange(sr) / sr
    y = .2 * np.sin(2 * np.pi * 330 * t)
    sf.write(path, np.stack([y, y], axis=1), sr)
    output = tmp_path / "mono.wav"
    meta = audio.decode(path, output, 10)
    assert meta["channels"] == 1 and meta["sample_rate"] == 22050
    assert meta["duration"] == pytest.approx(1, abs=.01)


def test_mp3_real_decode(tmp_path):
    source = tone(tmp_path / "source.wav", duration=1)
    mp3 = tmp_path / "encoded.mp3"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(source), str(mp3)],
                   check=True, capture_output=True)
    output = tmp_path / "decoded.wav"
    meta = audio.decode(mp3, output, 10)
    assert meta["duration"] == pytest.approx(1, abs=.1)
    assert meta["peak"] > .2

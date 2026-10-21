"""Explicit live check: python -m backend.check_ai --audio path/to/music.wav"""
import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import soundfile as sf

from .app.ai_provider import AIError, Provider, redact
from .app.config import Settings
from .app.music_ai import listen, wav_chunks


def check(settings, audio=None):
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "providers": {},
              "audio_test": {"status": "not_tested", "reason": "Supply --audio with a real music file."},
              "llm_test": {"status": "not_tested"}}
    for role in ("AUDIO", "LLM"):
        try:
            result["providers"][role] = {"status": "ok", **Provider.from_settings(settings, role).check_model(settings)}
        except AIError as exc:
            message = redact(settings, exc)
            logging.error("%s", message)
            result["providers"][role] = {"status": "failed", "error": message}
    if audio and result["providers"]["AUDIO"]["status"] != "ok":
        result["audio_test"]["reason"] = "Audio provider configuration/model check did not pass. No audio was sent."
    if audio and result["providers"]["AUDIO"]["status"] == "ok":
        try:
            # Accept only a short real WAV. Longer music is explicitly cropped to its first 5 seconds.
            path = Path(audio)
            if path.suffix.lower() != ".wav":
                raise AIError("Live probe requires a real WAV music file.")
            info = sf.info(path)
            end = min(info.duration, 5)
            data, _ = next(wav_chunks(path, 0, end, 30))
            cache = settings.storage_dir / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="mas-ai-check-", dir=cache) as folder:
                probe_settings = settings.model_copy(update={"storage_dir": Path(folder)})
                (Path(folder) / "audio").mkdir()
                (Path(folder) / "audio/probe.wav").write_bytes(data)
                observations = listen(probe_settings, Provider.from_settings(settings, "AUDIO"), [
                    {"file": "probe.wav", "kind": "original", "source_id": "probe", "track_id": "probe",
                     "source_range_seconds": [0, end], "range_seconds": [0, end], "offset_seconds": 0}], "ko")
            result["audio_test"] = {"status": "ok", "audio_input_verified": True,
                                    "source_range_seconds": [0, end], "observations": observations}
            if result["providers"]["LLM"]["status"] == "ok":
                try:
                    completion = Provider.from_settings(settings, "LLM").complete(settings, [
                        {"role": "system", "content": "Summarize these Music Flamingo observations in Korean. This is a Full Mix connection probe only; do not score vocals or claim a three-way analysis."},
                        {"role": "user", "content": json.dumps(observations, ensure_ascii=False)},
                    ], settings.llm_max_tokens)
                    result["llm_test"] = {"status": "ok", "three_way_pipeline_verified": False, **completion}
                except AIError as exc:
                    message = redact(settings, exc)
                    logging.error("LLM probe: %s", message)
                    result["llm_test"] = {"status": "failed", "error": message}
        except (AIError, OSError, ValueError, RuntimeError, StopIteration) as exc:
            message = redact(settings, exc)
            logging.error("Audio probe: %s", message)
            result["audio_test"] = {"status": "failed", "error": message}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, help="Real WAV music; sends at most its first 5 seconds.")
    parser.add_argument("--output", type=Path, help="Save sanitized verification JSON.")
    args = parser.parse_args()
    result = check(Settings(), args.audio)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0 if (all(p["status"] == "ok" for p in result["providers"].values())
                 and result["audio_test"]["status"] == result["llm_test"]["status"] == "ok") else 1


if __name__ == "__main__":
    sys.exit(main())
